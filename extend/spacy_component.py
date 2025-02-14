import pickle
import shelve
from typing import List, Dict, Optional, Set

import spacy
import torch
from classy.data.data_drivers import QASample
from classy.pl_modules.base import ClassyPLModule
from classy.utils.lightning import (
    load_prediction_dataset_conf_from_checkpoint,
    load_classy_module_from_checkpoint,
)
from spacy import Language

from spacy.tokens import Doc, Span

from extend.data.data_drivers import build_context
from extend.utils.sqlite3_mentions_inventory import Sqlite3BackedMentionsInventory


def load_checkpoint(checkpoint_path: str, device: int) -> ClassyPLModule:
    try:
        model = load_classy_module_from_checkpoint(checkpoint_path)
        if device >= 0:
            model.to(torch.device(device))
        model.freeze()
    except Exception:
        model = load_classy_module_from_checkpoint("/home/ubuntu/projects/extend/ExtEnD/experiments/extend-longformer-large/2021-10-22/09-11-39/checkpoints/best.ckpt")
        if device >= 0:
            model.to(torch.device(device))
        model.freeze()
    return model


def load_mentions_inventory(mentions_inventory_path: str) -> Dict:
    if mentions_inventory_path.endswith(".tsv"):
        inventory_stores = dict()
        with open(mentions_inventory_path) as f:
            for line in f:
                mention, *candidates = line.strip().split("\t")
                inventory_stores[mention] = candidates
    elif mentions_inventory_path.endswith(".sqlite3"):
        inventory_stores = Sqlite3BackedMentionsInventory.from_path(
            mentions_inventory_path
        )
    return inventory_stores


def annotate_doc(annotated_samples: List[QASample]):
    for annotated_sample in annotated_samples:
        start_index, end_index = annotated_sample.predicted_annotation
        annotated_sample.ne._.disambiguated_entity = annotated_sample.context[
            start_index:end_index
        ]


@Language.factory(
    "extend",
    default_config={
        "checkpoint_path": None,
        "mentions_inventory_path": None,
        "tokens_per_batch": 2000,
        "device": 0,
    },
)
class ExtendComponent:
    DEFAULT_ENTITY_WHITELIST = {
        "EVENT",
        "FAC",
        "GPE",
        "LANGUAGE",
        "LOC",
        "MONEY",
        "NORP",
        "ORG",
        "PERSON",
        "PRODUCT",
        "WORK_OF_ART",
    }

    def __init__(
        self,
        nlp,
        name,
        checkpoint_path: str,
        mentions_inventory_path: str,
        tokens_per_batch: int,
        device: int,
        entity_whitelist: Optional[Set[str]] = None,
    ):
        assert checkpoint_path is not None and mentions_inventory_path is not None, ""
        self.model = load_checkpoint(checkpoint_path, device)
        self.dataset_conf = load_prediction_dataset_conf_from_checkpoint(
            checkpoint_path
        )
        self.token_batch_size = tokens_per_batch
        self.mentions_inventory = load_mentions_inventory(mentions_inventory_path)
        self.entity_whitelist = entity_whitelist or self.DEFAULT_ENTITY_WHITELIST

    def _samples_from_doc(self, doc: Doc) -> List[QASample]:
        samples = []
        doc_tokens = [token.text for token in doc]
        for named_entity in doc.ents:
            if (
                named_entity.label_ in self.entity_whitelist
                and named_entity.lemma_ in self.mentions_inventory
            ):
                candidates = self.mentions_inventory[named_entity.lemma_]
                context, _, _ = build_context(candidates, answer=None)
                left_tokens = doc_tokens[: named_entity.start]
                right_tokens = doc_tokens[named_entity.end :]
                question = " ".join(
                    left_tokens + ["{", named_entity.text, "}"] + right_tokens
                )
                samples.append(
                    QASample(context, question, candidates=candidates, ne=named_entity)
                )
        return samples

    def __call__(self, doc: Doc) -> Doc:
        input_samples = self._samples_from_doc(doc)
        annotated_samples = self.model.predict(
            input_samples, self.dataset_conf, token_batch_size=self.token_batch_size
        )
        annotate_doc(annotated_samples)
        return doc


Span.set_extension("disambiguated_entity", default=None)
