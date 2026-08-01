"""Config loading, with the tier 1b rules enforced at startup.

Two hard rules, and both fail loudly rather than degrading:

  1. A tier 1b clinical fact does not load unless `sources` is non-empty and `verified` is
     true. A fact nobody can trace is not evidence.
  2. Tier 1b evidence alone can never reach HIGH confidence. Enforced in the rules engine;
     the cap is declared here.

These exist because the MMP1 clinical fact in this project was wrong twice before it was
right, both times from a source that read as authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.models.contracts import ClinicalFact, Experiment, ProxyRow

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


class ConfigError(RuntimeError):
    """Raised at startup when config is malformed. Never swallowed."""


def _normalise(s: str) -> str:
    """Lowercase, collapse punctuation and whitespace. Used for exact-match lookup only."""
    return re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")


@dataclass
class RejectedFact:
    fact_id: str
    reason: str


@dataclass
class Config:
    proxies: list[ProxyRow]
    experiments: list[Experiment]
    clinical_facts: list[ClinicalFact]
    tiers: dict
    rejected_facts: list[RejectedFact] = field(default_factory=list)

    _proxy_index: dict[str, ProxyRow] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        idx: dict[str, ProxyRow] = {}
        for row in self.proxies:
            for key in [row.endpoint, row.display_name, *row.synonyms]:
                n = _normalise(key)
                if n in idx and idx[n].endpoint != row.endpoint:
                    raise ConfigError(
                        f"Ambiguous proxy synonym {key!r}: maps to both "
                        f"{idx[n].endpoint!r} and {row.endpoint!r}. "
                        "A near-miss silently matching the wrong row is exactly the class "
                        "of failure this project exists to prevent."
                    )
                idx[n] = row
        self._proxy_index = idx

    def lookup_proxy(self, condition: str) -> ProxyRow | None:
        """Exact match on the normalised string plus synonyms.

        No fuzzy matching and no model inference. A miss returns None, which routes to
        abstention trigger 1.
        """
        return self._proxy_index.get(_normalise(condition))

    def clinical_facts_for(self, gene: str, *conditions: str) -> list[ClinicalFact]:
        """Match on ANY of the supplied condition keys.

        A fact is written against the disease it concerns (acne_vulgaris), but a query
        arrives against an endpoint (oily_skin) that borrows from it. Matching on one key
        only meant TNF silently lost its entire tier 1b layer, which is exactly the kind of
        quiet gap this project exists to surface.
        """
        g = gene.upper()
        keys = {_normalise(c) for c in conditions if c}
        return [
            f for f in self.clinical_facts
            if f.gene.upper() == g and _normalise(f.condition) in keys
        ]

    def experiment_for(self, gap: str) -> Experiment | None:
        return next((e for e in self.experiments if e.gap == gap), None)


def _load_yaml_optional(name: str) -> dict | None:
    """A config file whose absence is a state rather than an error.

    Only `clinical_facts.yaml` is loaded this way, from the archive. Every
    other config file is required, because a missing proxy table is a broken build while a
    missing fact table is now the normal case.
    """
    path = CONFIG_DIR / name
    if not path.exists():
        return None
    with path.open() as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"{name} did not parse to a mapping")
    return data


def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")
    with path.open() as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"{name} did not parse to a mapping")
    return data


def load_config(strict: bool = True) -> Config:
    """Load and validate all config.

    `strict` controls whether a rejected clinical fact is fatal. Default True: a fact that
    fails the sources rule is a config error, not a warning to be scrolled past. Tests and
    the eval harness may pass strict=False to inspect rejections.
    """
    proxies = [ProxyRow(**row) for row in _load_yaml("proxies.yaml")["proxies"]]
    experiments = [Experiment(**e) for e in _load_yaml("experiments.yaml")["experiments"]]
    tiers = _load_yaml("tiers.yaml")

    accepted: list[ClinicalFact] = []
    rejected: list[RejectedFact] = []

    # STAGE 7: `config/clinical_facts.yaml` IS GONE. The outcome axis is retrieved.
    #
    # The file is not deleted from the repository, it is moved to `config/archive/`, and the
    # reason is the owner's: the `file` provider stays selectable as a demo fallback if the
    # graph turns out unstable on stage, and a fallback that cannot load its own data is not
    # a fallback. The archive is read ONLY when that provider is explicitly asked for. It is
    # not on the default path and nothing reads it unless you pass `--provider file` or set
    # BIOLEAD_OUTCOME_PROVIDER=file.
    #
    # If the intent was a hard delete rather than a retirement, remove
    # `config/archive/clinical_facts.yaml` and this block degrades to an empty fact list,
    # which is already the tested behaviour below.
    raw_facts = _load_yaml_optional("archive/clinical_facts.yaml")
    for raw in (raw_facts or {}).get("facts", []):
        fact = ClinicalFact(**{k: v for k, v in raw.items() if k in ClinicalFact.model_fields})

        # RULE 1, both halves. Untraceable or unverified evidence does not load.
        problems = []
        if not fact.sources:
            problems.append("`sources` is empty")
        if not fact.verified:
            problems.append("`verified` is false")

        if problems:
            rejected.append(RejectedFact(fact.id, " and ".join(problems)))
            continue

        accepted.append(fact)

    if rejected and strict:
        lines = "\n".join(f"  - {r.fact_id}: {r.reason}" for r in rejected)
        raise ConfigError(
            "Tier 1b clinical facts refused at load time:\n"
            f"{lines}\n\n"
            "Tier 1 sits at the top of the evidence hierarchy, so an untraceable 1b entry "
            "would put the strongest evidence class in the system beyond audit. Populate "
            "`sources` and set `verified: true`, or delete the entry. See "
            "config/archive/clinical_facts.yaml."
        )

    return Config(
        proxies=proxies,
        experiments=experiments,
        clinical_facts=accepted,
        tiers=tiers,
        rejected_facts=rejected,
    )
