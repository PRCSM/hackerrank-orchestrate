"""Find every resource that belongs to a case's subject.

Three grounded linkage channels, in decreasing strength:

1. key       - an observation of the resource carries one of the subject's
               identifiers (email, historical email, phone, email hash,
               user_id, LINK_RESOURCE_IDENTITY catalog rows).
2. recreation- an activity shows a data flow / backup repopulating one of the
               subject's key-linked resources.
3. batch     - the deletion worker processes a request in one batch shortly
               before the workflow reports its status (`reported_at`). A
               resource handled in that batch that no observation ties to
               anybody (a 404 account, a fully anonymised analytics profile)
               is attributed to the request whose batch window contains it.
               When several requests share a batch, the resource goes to the
               request whose own evidence files also observe it; remaining
               ties are resolved by matching processing order to case order
               and are flagged as uncertain for the agent layer.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from graph.evidence_graph import EvidenceGraph
from graph.identity import Identity, IdentityResolver
from ingest.observation import RESOURCE_ID_PATTERN, Observation, system_for_resource
from ingest.parse_activity import Activity
from ingest.requests import DeletionRequest, case_sort_key

# How the deletion worker phrases batch work that targets the subject's own
# records, and the window around reported_at in which it happens.
PROFILE_SUBMISSION = ("submitted primary profile", "target resource")
POLICY_COMPLETION = ("policy action completed", "reported the requested operation as completed")
PROFILE_WINDOW = (timedelta(hours=2), timedelta(minutes=15))
POLICY_WINDOW = (timedelta(hours=1), timedelta(minutes=5))
WORKER = "deletion_worker"


@dataclass
class Link:
    resource_id: str
    system_id: str
    channel: str  # key | recreation | batch
    reason: str
    uncertain: bool = False


@dataclass
class CaseLinks:
    case_id: str
    identities: list[Identity]
    links: dict[str, Link] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)

    def add(self, link: Link) -> None:
        self.links.setdefault(link.resource_id, link)


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def resource_type(resource_id: str) -> str:
    match = RESOURCE_ID_PATTERN.match(resource_id)
    return match.group(1) if match else resource_id.rsplit("_", 1)[0]


def _owners_of(observations: list[Observation], resolver: IdentityResolver) -> set[str]:
    owners: set[str] = set()
    for obs in observations:
        for key in obs.keys:
            owners |= resolver.owners(key)
    return owners


class Linker:
    def __init__(self, graph: EvidenceGraph, resolver: IdentityResolver) -> None:
        self.graph = graph
        self.resolver = resolver

    # ------------------------------------------------------------ key + recreation
    def direct_links(self, request: DeletionRequest) -> CaseLinks:
        identities = self.resolver.resolve(request.email, request.audit_at)
        result = CaseLinks(case_id=request.case_id, identities=identities)
        keys = frozenset().union(*(i.keys for i in identities)) if identities else frozenset()
        for obs in sorted(self.graph.observations_for_keys(keys, request.audit_at), key=lambda o: o.resource_id):
            if self._stale(obs.resource_id, request):
                if obs.resource_id not in result.stale:
                    result.stale.append(obs.resource_id)
                continue
            matched = sorted(obs.keys & keys)
            result.add(Link(obs.resource_id, obs.system_id, "key", f"{obs.evidence_id} carries {matched[0]}"))
        for resource_id in sorted(list(result.links)):
            for source in sorted(self.graph.recreation_sources.get(resource_id, set())):
                result.add(Link(source, system_for_resource(source), "recreation", f"{source} repopulated {resource_id}"))
        return result

    def _stale(self, resource_id: str, request: DeletionRequest) -> bool:
        """Nothing about the resource was observed since the request was made:
        it belongs to an earlier episode (an earlier deletion of the same
        person) and says nothing about the copies this request is about."""
        times = [o.observed_at for o in self.graph.resource_observations(resource_id, request.audit_at)]
        return bool(times) and max(times) < request.requested_at

    # ------------------------------------------------------------ batch inference
    def link_all(self, requests: list[DeletionRequest], violates: Callable[[DeletionRequest, CaseLinks], bool]) -> dict[str, CaseLinks]:
        """Direct links for every request, then batch attribution across them.

        `violates(request, links)` tells whether the request's directly linked
        records already show a policy violation; completed policy actions are
        attributed to requests without one first (their batch succeeded)."""
        links = {r.case_id: self.direct_links(r) for r in requests}
        self._violating = {r.case_id: violates(r, links[r.case_id]) for r in requests}
        by_case = {r.case_id: r for r in requests}
        worker = [a for a in self.graph.activities if a.source_system == WORKER and RESOURCE_ID_PATTERN.match(a.resource_id)]
        claims: dict[str, list[tuple[str, Activity]]] = defaultdict(list)
        for request in requests:
            for activity in self._batch(request, worker):
                if self._claimable(request, links[request.case_id], activity.resource_id):
                    claims[activity.resource_id].append((request.case_id, activity))
        self._assign(claims, links, by_case)
        self.claims, self.links, self.by_case = claims, links, by_case
        return links

    def batch_candidates(self, case_id: str) -> list[dict]:
        """Batch rows this case claims, with rivals and who got each (agent tool)."""
        case = self.links[case_id]
        files = self._evidence_files(case, self.by_case[case_id].audit_at)
        owner = {r: c for c, l in self.links.items() for r, link in l.links.items() if link.channel == "batch"}
        rows = []
        for resource_id, claimants in sorted(self.claims.items()):
            mine = [a for c, a in claimants if c == case_id]
            if not mine:
                continue
            seen = {o.evidence_id for o in self.graph.resource_observations(resource_id, self.by_case[case_id].audit_at)}
            rows.append({
                "resource_id": resource_id, "activity_id": mine[0].activity_id, "event_time": mine[0].event_time,
                "competing_requests": sorted((c for c, _ in claimants if c != case_id), key=case_sort_key),
                "observed_in_subject_evidence": sorted(seen & files), "assigned_to": owner.get(resource_id, ""),
            })
        return rows

    def _batch(self, request: DeletionRequest, worker: list[Activity]) -> list[Activity]:
        """Worker rows in the request's batch: the primary-profile submission
        (accounts) and policy actions that completed (records whose
        identifiers were already removed, so nothing else can link them)."""
        reported = _parse(request.reported_at)
        batch = []
        for activity in worker:
            if activity.event_time > request.audit_at:
                continue
            details = activity.details.lower()
            is_account = resource_type(activity.resource_id) == "account"
            phrases, (before, after) = (PROFILE_SUBMISSION, PROFILE_WINDOW) if is_account else (POLICY_COMPLETION, POLICY_WINDOW)
            if any(p in details for p in phrases) and reported - before <= _parse(activity.event_time) <= reported + after:
                batch.append(activity)
        return batch

    def _claimable(self, request: DeletionRequest, case: CaseLinks, resource_id: str) -> bool:
        if resource_id in case.links:
            return False
        rtype = resource_type(resource_id)
        if any(resource_type(r) == rtype for r in case.links):
            return False
        owners = _owners_of(self.graph.resource_observations(resource_id, request.audit_at), self.resolver)
        return not owners

    def _evidence_files(self, case: CaseLinks, audit_at: str) -> set[str]:
        files: set[str] = set()
        for resource_id in case.links:
            files |= {o.evidence_id for o in self.graph.resource_observations(resource_id, audit_at) if o.source_type == "file"}
        return files

    def _assign(self, claims, links, by_case) -> None:
        """Per resource type, match claimed resources to claiming cases.

        Edge cost is (file, violation). file=0: the case's own evidence files
        also observe the resource; file=1: only the batch connects them, used
        for accounts (every case has one) and for resources no other request
        claims. violation=1: the case already shows a policy violation, so its
        batch did not complete cleanly; a clean case gets first pick of the
        records its own evidence observes (it needs them as proof). Within a
        level, cases in case order each take the earliest-processed remaining
        resource they claim. A case takes one inferred resource per type and
        a resource goes to one case; a choice with an equally good rival is
        marked uncertain."""
        by_type: dict[str, list[str]] = defaultdict(list)
        for resource_id in claims:
            by_type[resource_type(resource_id)].append(resource_id)
        for rtype in sorted(by_type):
            cases = sorted({c for r in by_type[rtype] for c, _ in claims[r]}, key=case_sort_key)
            resources = sorted(by_type[rtype], key=lambda r: (min(a.event_time for _, a in claims[r]), r))
            files = {c: self._evidence_files(links[c], by_case[c].audit_at) for c in cases}

            def cost(case_id: str, resource_id: str) -> tuple[int, int]:
                seen = {o.evidence_id for o in self.graph.resource_observations(resource_id, by_case[case_id].audit_at)}
                file_cost = 0 if seen & files[case_id] else 1
                return file_cost, int(self._violating[case_id]) if file_cost == 0 else 0

            used: set[str] = set()
            for level in ((0, 0), (0, 1), (1, 0), (1, 1)):
                for case_id in cases:
                    if any(resource_type(r) == rtype for r in links[case_id].links):
                        continue
                    options = [r for r in resources if r not in used and case_id in {c for c, _ in claims[r]}
                               and cost(case_id, r) == level
                               and (level[0] == 0 or rtype == "account" or len(claims[r]) == 1)]
                    if not options:
                        continue
                    resource_id = options[0]
                    rivals = [c for c, _ in claims[resource_id] if c != case_id and cost(c, resource_id) == level
                              and not any(resource_type(r) == rtype for r in links[c].links)]
                    activity = next(a for c, a in claims[resource_id] if c == case_id)
                    links[case_id].add(Link(
                        resource_id, system_for_resource(resource_id), "batch",
                        f"{activity.activity_id} handled {resource_id} in the deletion batch before reported_at",
                        uncertain=bool(rivals) or len(options) > 1,
                    ))
                    used.add(resource_id)

