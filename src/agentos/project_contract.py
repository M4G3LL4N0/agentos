"""Project contracts: formal boundaries for autonomous work on a project.

A contract pins scope, evidence requirements, verification rules,
authorization grants, and cost ceilings. All enforcement is fail-closed:
a missing or expired contract blocks consequential actions rather than
silently proceeding.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentos.engine import AgentOSError
from agentos.models import utc_now_iso


class ContractStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    VIOLATED = "VIOLATED"
    CLOSED = "CLOSED"


@dataclass
class EvidenceRule:
    kind: str                 # e.g. "verification", "human_approval", "usage_entry"
    required: bool = True
    min_count: int = 1


@dataclass
class Authorization:
    grant: str                # e.g. "execute", "verify", "paid", "push"
    conditions: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectContract:
    contract_id: str
    project_id: str
    scope: list[str] = field(default_factory=list)          # allowed operations
    evidence_rules: list[EvidenceRule] = field(default_factory=list)
    authorizations: list[Authorization] = field(default_factory=list)
    cost_ceiling_usd: float = 0.0
    max_duration_seconds: float = 3600.0
    free_only: bool = True
    status: str = ContractStatus.DRAFT
    created_at: str = field(default_factory=utc_now_iso)
    activated_at: str = ""
    expires_at: str = ""      # epoch seconds as string; empty = no expiry
    violations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "project_id": self.project_id,
            "scope": list(self.scope),
            "evidence_rules": [asdict(r) for r in self.evidence_rules],
            "authorizations": [asdict(a) for a in self.authorizations],
            "cost_ceiling_usd": self.cost_ceiling_usd,
            "max_duration_seconds": self.max_duration_seconds,
            "free_only": self.free_only,
            "status": self.status,
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "expires_at": self.expires_at,
            "violations": list(self.violations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectContract:
        rules = [EvidenceRule(**r) for r in data.get("evidence_rules", [])]
        auths = [Authorization(**a) for a in data.get("authorizations", [])]
        return cls(
            contract_id=data["contract_id"],
            project_id=data["project_id"],
            scope=list(data.get("scope", [])),
            evidence_rules=rules,
            authorizations=auths,
            cost_ceiling_usd=float(data.get("cost_ceiling_usd", 0.0)),
            max_duration_seconds=float(data.get("max_duration_seconds", 3600.0)),
            free_only=bool(data.get("free_only", True)),
            status=str(data.get("status", ContractStatus.DRAFT)),
            created_at=str(data.get("created_at", utc_now_iso())),
            activated_at=str(data.get("activated_at", "")),
            expires_at=str(data.get("expires_at", "")),
            violations=list(data.get("violations", [])),
        )


class ContractStore:
    """File-backed contract registry under the AgentOS home directory."""

    def __init__(self, home: Path) -> None:
        self.dir = Path(home) / "contracts"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, contract_id: str) -> Path:
        return self.dir / f"{contract_id}.json"

    def save(self, contract: ProjectContract) -> None:
        tmp = self._path(contract.contract_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(contract.to_dict(), indent=2))
        tmp.replace(self._path(contract.contract_id))

    def get(self, contract_id: str) -> ProjectContract | None:
        path = self._path(contract_id)
        if not path.exists():
            return None
        try:
            return ProjectContract.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def list_for_project(self, project_id: str) -> list[ProjectContract]:
        contracts: list[ProjectContract] = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                if data.get("project_id") == project_id:
                    contracts.append(ProjectContract.from_dict(data))
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return contracts

    def active_for_project(self, project_id: str) -> ProjectContract | None:
        # returns ACTIVE contracts even if expired — ContractEnforcer
        # performs the expiry check so it can mark the contract EXPIRED.
        for contract in self.list_for_project(project_id):
            if contract.status == ContractStatus.ACTIVE:
                return contract
        return None


def _expired(contract: ProjectContract) -> bool:
    if not contract.expires_at:
        return False
    try:
        return time.time() > float(contract.expires_at)
    except ValueError:
        return True  # unparsable expiry => fail closed


class ContractEnforcer:
    """Fail-closed checks for consequential actions under a contract."""

    def __init__(self, store: ContractStore) -> None:
        self.store = store

    def check_action(
        self,
        project_id: str,
        operation: str,
        cost_estimate_usd: float = 0.0,
        spent_usd: float = 0.0,
        evidence_count: dict[str, int] | None = None,
    ) -> tuple[bool, str]:
        """Return (allowed, reason). False when blocked."""
        contract = self.store.active_for_project(project_id)
        if contract is None:
            return False, f"no active contract for project {project_id}"
        if _expired(contract):
            contract.status = ContractStatus.EXPIRED
            self.store.save(contract)
            return False, f"contract {contract.contract_id} expired"
        if contract.scope and operation not in contract.scope:
            self._violate(contract, f"out-of-scope operation: {operation}")
            return False, f"operation {operation} outside contract scope"
        if cost_estimate_usd > 0.0 and contract.free_only:
            self._violate(contract, f"paid operation blocked: {operation}")
            return False, f"paid operations blocked by free_only contract"
        if spent_usd + cost_estimate_usd > contract.cost_ceiling_usd:
            self._violate(contract, f"cost ceiling breach: {operation}")
            return False, f"cost ceiling ${contract.cost_ceiling_usd:g} would be exceeded"
        counts = evidence_count or {}
        for rule in contract.evidence_rules:
            if rule.required and counts.get(rule.kind, 0) < rule.min_count:
                return False, f"missing required evidence: {rule.kind}"
        return True, "allowed"

    def has_authorization(self, project_id: str, grant: str) -> bool:
        contract = self.store.active_for_project(project_id)
        if contract is None or _expired(contract):
            return False
        return any(a.grant == grant for a in contract.authorizations)

    def _violate(self, contract: ProjectContract, detail: str) -> None:
        contract.violations.append({"at": utc_now_iso(), "detail": detail})
        contract.status = ContractStatus.VIOLATED
        self.store.save(contract)

    def close(self, project_id: str) -> ProjectContract | None:
        contract = self.store.active_for_project(project_id)
        if contract is None:
            return None
        contract.status = ContractStatus.CLOSED
        self.store.save(contract)
        return contract


def draft_contract(
    contract_id: str,
    project_id: str,
    scope: list[str],
    *,
    free_only: bool = True,
    cost_ceiling_usd: float = 0.0,
    authorizations: list[str] | None = None,
    evidence: list[EvidenceRule] | None = None,
) -> ProjectContract:
    """Convenience builder for a DRAFT contract (not yet active)."""
    return ProjectContract(
        contract_id=contract_id,
        project_id=project_id,
        scope=list(scope),
        evidence_rules=evidence or [EvidenceRule(kind="verification")],
        authorizations=[Authorization(grant=g) for g in (authorizations or [])],
        cost_ceiling_usd=cost_ceiling_usd,
        free_only=free_only,
    )


def activate(contract: ProjectContract, store: ContractStore) -> ProjectContract:
    """Activate a draft contract (raises if already violated/closed)."""
    if contract.status == ContractStatus.VIOLATED:
        raise AgentOSError("cannot activate a violated contract")
    if contract.status == ContractStatus.CLOSED:
        raise AgentOSError("cannot activate a closed contract")
    contract.status = ContractStatus.ACTIVE
    contract.activated_at = utc_now_iso()
    store.save(contract)
    return contract
