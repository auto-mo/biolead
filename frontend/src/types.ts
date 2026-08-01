export type Provenance = "RETRIEVED" | "ASSERTED";
export interface EvidenceItem {
  tier: string; source: string; datasource_id: string | null;
  summary: string; supports: string; provenance: Provenance;
  retrieved_at: string | null; data_version: string | null;
  raw?: { nct_id?: string | null; sources?: string[]; caveats?: string[] };
}
/** One trial behind an outcome state, so a reader can confirm it rather than trust it. */
export interface OutcomeTrial {
  nct_id: string;
  drug: string | null;
  status: string | null;
  has_results: boolean;
  enrollment: number | null;
  title: string | null;
  read?: string | null;
  path?: string | null;
}
/** A trial the attribution cap set aside, and why. Rendered, not merely recorded. */
export interface ExcludedTrial {
  nct_id: string;
  drug: string | null;
  named_targets: string[];
  why: string;
}
export interface Verdict {
  position: string; targetability: string; confidence: string | null;
  rule_fired: string | null; reasoning: string; cited_tiers: string[];
  outcome_measured_on: string | null;
  outcome_state: "NO_DRUG" | "TESTED_UNREPORTED" | "TESTED_REPORTED" | "NOT_ASSESSED";
  outcome_trials: OutcomeTrial[];
  outcome_path: string | null;
  outcome_reason: string | null;
  /** file | retrieved | graph. Three exist and they disagree; only `graph` applies the
   *  attribution cap and the disagreement rule. */
  outcome_provider: string;
  outcome_consensus: string | null;
  outcome_split: string | null;
  outcome_minority: OutcomeTrial[];
  outcome_excluded: ExcludedTrial[];
  outcome_measured_disease: string | null;
  outcome_borrowed: boolean;
  text_technical: string; text_plain: string;
}
/** One row of config/proxies.yaml, served by /api/proxies. */
export interface ProxyRow {
  endpoint: string; display_name: string;
  formal_name: string | null; plain_name: string | null;
  synonyms: string[];
  borrow_type: "NO_BORROW_NEEDED" | "DISEASE_BORROW" | "NONE";
  borrowed_from: string | null;
  rating: string; rationale: string; what_it_misses: string;
  population_caveat: string | null; refuse: boolean;
}
/** One candidate from /api/genes. The list is ranked and fuzzy; `exact` marks a symbol match. */
export interface GeneHit {
  symbol: string; name: string; ensembl_id: string; exact: boolean;
}
export type Reach = "REACHABLE" | "HARD_TO_REACH" | "OUT_OF_REACH" | "UNKNOWN";
export interface Reachability {
  verdict: Reach;
  rule_fired: string;
  depth: "EPIDERMAL" | "APPENDAGEAL" | "DERMAL" | null;
  in_skin: boolean | null;
  skin_ntpm: number | null;
  skin_ih: string | null;
  compartments: Record<string, string>;
  subcellular: string[];
  secretome_location: string | null;
  small_molecule_buckets: string[];
  antibody_buckets: string[];
  sm_clinical: string[];
  sm_structural: string[];
  ab_clinical: string[];
  ab_location_only: string[];
  supports: string[];
  blockers: string[];
  unknowns: string[];
}

export type EvidenceClass =
  | "TIER_1_OR_2"
  | "EXPRESSION_OR_LITERATURE_ONLY"
  | "NO_ASSOCIATION"
  | "OTHER_EVIDENCE_ONLY"
  | "NOT_ASSESSABLE"
  | "COULD_NOT_CHECK";

export interface BatchRow {
  gene: string;
  ensembl_id: string | null;
  evidence_class: EvidenceClass;
  verdict: Verdict | null;
  mode: string | null;
  tier_counts: Record<string, number>;
  datatype_scores: Record<string, number>;
  input_fields: Record<string, string>;
  note: string;
  reachability: Reachability | null;
}
export interface BatchSummary {
  condition_as_typed: string;
  resolved_disease_name: string | null;
  resolved_disease_id: string | null;
  proxy: ProxyRow | null;
  input_count: number;
  assessable_count: number;
  tier_1_or_2: number;
  expression_or_literature_only: number;
  no_association: number;
  other_evidence_only: number;
  not_assessable: number;
  could_not_check: number;
  reach_reachable: number;
  reach_hard: number;
  reach_out: number;
  reach_unknown: number;
  counts_trustworthy: boolean;
  trust_note: string | null;
}
export interface BatchResult {
  summary: BatchSummary;
  rows: BatchRow[];
  source: string | null;
  limitations: string[];
  limiter_stats: {
    source: string; max_concurrent: number; rate_per_second: number;
    calls: number; retries: number; failures: number;
    throttled_seconds: number; peak_concurrency: number;
  }[];
  cache_stats: Record<string, number | null>;
  call_count: number;
  elapsed_seconds: number | null;
  data_version: string | null;
  assessed_at: string | null;
}
export interface Preset {
  id: string; label: string; condition: string; count: number;
  citation: string; url: string; what_it_is: string;
  truncation_note: string | null;
}

export interface Assessment {
  gene: string; ensembl_id: string | null;
  condition_as_typed: string;
  resolved_disease_id: string | null; resolved_disease_name: string | null;
  term_substituted: boolean; mode: string; mode_reason: string;
  proxy: ProxyRow | null;
  tier_profile: { tiers: Record<string, EvidenceItem[]>; checked_and_empty: string[]; could_not_check: string[] };
  conflicts: string[]; final_verdict: Verdict;
  model_verdict: Verdict | null; agreement: boolean | null;
  limitations: string[];
  resolving_experiment: { label: string; assay: string; what_a_positive_would_change: string } | null;
  reachability: Reachability | null;
  text_technical: string; text_plain: string;
  adjudicator_is_stub: boolean;
  data_version: string | null; assessed_at: string | null;
}
