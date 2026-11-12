from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from solution.config import DEFAULT_SCHEMAS_DIR
from solution.normalizers import normalize_header


@dataclass
class SchemaField:
    name: str
    type: str  # 'string', 'phone', 'date', 'decimal', 'enum'
    required: bool = False
    pii: bool = False
    reference: bool = False
    aliases: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    default: str | None = None


@dataclass
class WorkflowSchema:
    workflow_id: str
    description: str
    fields: list[SchemaField]
    reference_field: str
    # Map from normalized column name/alias -> SchemaField
    header_map: dict[str, SchemaField] = field(default_factory=dict)


@dataclass
class DispositionRule:
    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


class SchemaRegistry:
    def __init__(self, schemas_dir: Path | str = DEFAULT_SCHEMAS_DIR):
        self.schemas_dir = Path(schemas_dir)
        self.workflows: dict[str, WorkflowSchema] = {}
        self.dispositions: dict[tuple[str, str], DispositionRule] = {}
        self._load_all()

    def _load_all(self):
        # 1. Load workflow schemas (*.json)
        for json_file in self.schemas_dir.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            wf_id = data["workflow_id"]
            fields: list[SchemaField] = []
            ref_field = ""
            header_map: dict[str, SchemaField] = {}

            for f_data in data.get("fields", []):
                sf = SchemaField(
                    name=f_data["name"],
                    type=f_data["type"],
                    required=f_data.get("required", False),
                    pii=f_data.get("pii", False),
                    reference=f_data.get("reference", False),
                    aliases=f_data.get("aliases", []),
                    values=f_data.get("values", []),
                    default=f_data.get("default", None),
                )
                fields.append(sf)
                if sf.reference:
                    ref_field = sf.name

                # Add self name
                header_map[normalize_header(sf.name)] = sf
                # Add aliases
                for alias in sf.aliases:
                    header_map[normalize_header(alias)] = sf

            wf_schema = WorkflowSchema(
                workflow_id=wf_id,
                description=data.get("description", ""),
                fields=fields,
                reference_field=ref_field,
                header_map=header_map,
            )
            self.workflows[wf_id] = wf_schema

        # 2. Load dispositions.csv
        disp_file = self.schemas_dir / "dispositions.csv"
        if disp_file.exists():
            with open(disp_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    wf = row["workflow_id"].strip()
                    code = row["code"].strip()
                    is_term = row["is_terminal"].strip().lower() == "true"
                    is_succ = row["is_success"].strip().lower() == "true"
                    self.dispositions[(wf, code)] = DispositionRule(
                        workflow_id=wf,
                        code=code,
                        label=row["label"].strip(),
                        is_terminal=is_term,
                        is_success=is_succ,
                    )

    def get_workflow(self, workflow_id: str) -> WorkflowSchema | None:
        return self.workflows.get(workflow_id)

    def get_disposition(self, workflow_id: str, code: str) -> DispositionRule | None:
        return self.dispositions.get((workflow_id, code))
