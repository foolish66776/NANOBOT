"""Pre-validatore deterministico JSON n8n.

Eseguito dopo ogni build, prima del review LLM.
Cattura errori di formato strutturale senza consumare token.
"""

from __future__ import annotations

import re
import uuid


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def validate_n8n_json(wf: dict) -> list[str]:
    """Restituisce lista di errori tecnici n8n. Lista vuota = struttura valida.

    Controlla solo regole di formato oggettive — non logica di business.
    """
    errors: list[str] = []

    # --- settings.timezone ---
    settings = wf.get("settings") or {}
    if not settings.get("timezone"):
        errors.append(
            "settings.timezone mancante o null — aggiungilo: "
            '"settings": {"executionOrder": "v1", "timezone": "Europe/Rome"}'
        )

    # --- campi vietati al top level ---
    forbidden = {"id", "createdAt", "updatedAt", "active", "meta", "tags"}
    for f in forbidden & wf.keys():
        errors.append(
            f"Campo '{f}' non accettato dall'API n8n su create/update — rimuovilo"
        )

    node_names: set[str] = set()
    node_ids: list[str] = []

    for node in wf.get("nodes", []):
        name = node.get("name") or "<nodo senza nome>"
        ntype = node.get("type", "")
        node_names.add(name)

        # UUID format
        nid = node.get("id", "")
        if nid:
            node_ids.append(nid)
            if not _UUID_RE.match(nid):
                errors.append(
                    f"Nodo '{name}': id={nid!r} non è un UUID v4 valido"
                )

        # ---- Schedule Trigger ----
        if ntype == "n8n-nodes-base.scheduleTrigger":
            tv = node.get("typeVersion")
            if tv != 1.2:
                errors.append(
                    f"Nodo '{name}': typeVersion={tv!r} — deve essere 1.2"
                )

            params = node.get("parameters", {})

            # timezone nel posto sbagliato
            if "timezone" in params:
                errors.append(
                    f"Nodo '{name}': 'timezone' nei parametri del nodo — "
                    "deve stare solo in settings.timezone"
                )

            # field deve essere cronExpression
            intervals = params.get("rule", {}).get("interval", [])
            for i, interval in enumerate(intervals):
                field = interval.get("field")
                if field != "cronExpression":
                    errors.append(
                        f"Nodo '{name}' interval[{i}]: field={field!r} — "
                        "deve essere 'cronExpression'"
                    )

        # ---- HTTP Request GET: no queryParameters separati ----
        if ntype == "n8n-nodes-base.httpRequest":
            params = node.get("parameters", {})
            method = params.get("method", "GET").upper()
            if method == "GET":
                qp = params.get("queryParameters")
                if isinstance(qp, dict) and "parameters" in qp:
                    errors.append(
                        f"Nodo '{name}': usa queryParameters separati per GET — "
                        "incorpora i parametri direttamente nell'URL "
                        "(es. ?lat=44&lon=7)"
                    )

    # ---- connections: chiavi devono essere nomi nodo ----
    for key in wf.get("connections", {}):
        if key not in node_names:
            errors.append(
                f"connections['{key}']: nessun nodo con questo nome. "
                f"Nomi validi: {sorted(node_names)}"
            )

    # ---- node ids univoci ----
    seen: set[str] = set()
    for nid in node_ids:
        if nid in seen:
            errors.append(f"ID nodo duplicato: {nid!r}")
        seen.add(nid)

    return errors
