"""P0-10 image bake-off (thin CLI over images.generator + images.postprocess).

Reads the product list from the store repo (fixtures/samples/p0-10-bakeoff.yaml), generates
images for each product, and writes the post-processed images plus a report into OUT.

  --stage main       every model in images.bakeoff_candidates, images.variations_per_role each
                     (--chosen: only images.model, for products added after the comparison)
  --stage lifestyle  only images.model (set after the main-image comparison)
  --products         limit the run to these product ids

A reference may be {url, save_as}: it is downloaded once from a public URL (for example the
store's CDN image of an existing product) into the store repo, then used like a local file.

Costs go through a persisted ledger (fixtures/ledger/p0-gemini.json), and the run stops before
a call that would take total Gemini spend past costs.phase0_budget.gemini.
Never writes to the store. Never logs the key or image bytes.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import yaml

from autolist.core.config import get_secret, load_store_config, store_dir
from autolist.core.cost_ledger import CostLedger, JsonFileLedgerStore
from autolist.core.logging import configure_logging
from autolist.core.models import ImageRequest, ReferenceImage
from autolist.images.errors import BillingRequired, ImageError
from autolist.images.generator import GeminiImageGenerator
from autolist.images.postprocess import postprocess

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
SCENE_RULES = "Office setting. No people (open question #19 is not answered yet)."


def _fetch(samples: Path, out_root: Path, ref: dict[str, str]) -> str:
    """Download a public reference image once; return its path relative to samples."""
    rel = ref["save_as"]
    for base in (samples, out_root / "fixtures" / "samples"):
        if (base / rel).is_file():
            return rel
    url = ref["url"]
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError(f"reference url must be https: {url}")
    r = httpx.get(url, timeout=60, follow_redirects=False)
    r.raise_for_status()
    dest = out_root / "fixtures" / "samples" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return rel


def _refs(bases: list[Path], names: list[str]) -> list[ReferenceImage]:
    out = []
    for name in names:
        path = next((b / name for b in bases if (b / name).is_file()), bases[0] / name)
        mime = _MIME[path.suffix.lower()]
        out.append(ReferenceImage(name=path.name, mime_type=mime, data=path.read_bytes()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--stage", choices=["main", "lifestyle"], required=True)
    ap.add_argument("--chosen", action="store_true", help="main stage with images.model only")
    ap.add_argument("--products", nargs="*", default=None)
    args = ap.parse_args()
    configure_logging()

    cfg = load_store_config()
    root = store_dir()
    samples = root / "fixtures" / "samples"
    products: dict[str, Any] = yaml.safe_load(
        (samples / "p0-10-bakeoff.yaml").read_text(encoding="utf-8")
    )["products"]
    if args.products:
        unknown = set(args.products) - set(products)
        if unknown:
            raise SystemExit(f"unknown products: {sorted(unknown)}")
        products = {k: v for k, v in products.items() if k in args.products}
    if args.stage == "main" and args.chosen:
        models = [cfg.require("images.model")]
        prompt_name, role = "image_main", "main"
    elif args.stage == "main":
        models = list(cfg.images.bakeoff_candidates)
        prompt_name, role = "image_main", "main"
    else:
        models = [cfg.require("images.model")]
        prompt_name, role = "image_lifestyle", "lifestyle"
    variations = cfg.images.variations_per_role
    budget = cfg.costs.phase0_budget.gemini

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    ledger_path = args.out / "fixtures" / "ledger" / "p0-gemini.json"
    ledger = CostLedger(cfg.costs, cfg.store.timezone, JsonFileLedgerStore(ledger_path))
    run_dir = args.out / "fixtures" / "bakeoff" / "p0-10" / f"{stamp}-{role}"
    gen = GeminiImageGenerator(cfg, get_secret(cfg, "gemini_api_key"), ledger)

    def spent() -> Decimal:
        return sum(
            (
                e.usd
                for e in JsonFileLedgerStore(ledger_path).load().entries
                if e.provider == "gemini"
            ),
            Decimal(0),
        )

    start_spend = spent()
    rows: list[dict[str, Any]] = []
    shape: Any = None
    stopped = ""
    bases = [samples, args.out / "fixtures" / "samples"]
    for pid, product in products.items():
        names = [
            r if isinstance(r, str) else _fetch(samples, args.out, r) for r in product["references"]
        ]
        refs = _refs(bases, names)
        variables = {
            "product_facts": product["product_facts"].strip(),
            "reference_notes": product["reference_notes"].strip(),
        }
        if role == "lifestyle":
            variables["scene_rules"] = SCENE_RULES
        for model in models:
            per_image = cfg.images.prices[model].per_image_usd[cfg.require("images.generate_size")]
            for v in range(1, variations + 1):
                if spent() + per_image > budget:
                    stopped = f"phase-0 Gemini budget {budget} USD would be exceeded"
                    break
                row: dict[str, Any] = {"product": pid, "model": model, "variation": v}
                try:
                    img = gen.generate(
                        ImageRequest(
                            job_id=f"p0-10-{pid}",
                            model=model,
                            role=role,
                            prompt_name=prompt_name,
                            variables=variables,
                            references=refs,
                        )
                    )
                except BillingRequired as exc:
                    stopped = str(exc)
                    break
                except ImageError as exc:
                    row.update(error=f"{type(exc).__name__}: {exc}")
                    if getattr(exc, "text", ""):
                        row["model_text"] = str(exc.text)[:300]  # type: ignore[attr-defined]
                else:
                    out = run_dir / pid / f"{role}-{model}-v{v}.jpg"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(postprocess(img.data, cfg.images.output_px))
                    row.update(
                        file=str(out.relative_to(args.out)),
                        raw_px=list(img.size_px),
                        usd=str(img.usd),
                        estimated=img.estimated_cost,
                        finish=img.finish_reason,
                        model_text=" ".join(img.text_parts)[:300],
                    )
                if shape is None and gen.last_shape is not None:
                    shape = gen.last_shape
                rows.append(row)
            if stopped:
                break
        if stopped:
            break
    gen.close()

    lines = [
        f"# P0-10 image bake-off ({role}) - {stamp}",
        "",
        f"- models: {models}; variations: {variations}; prompt: {prompt_name}",
        f"- Gemini spend this run: {spent() - start_spend:.4f} USD",
        f"- phase-0 Gemini total: {spent():.4f} / {budget} USD",
        f"- stopped early: {stopped or 'no'}",
        "",
        "## Results",
        "",
        "| product | model | v | result | raw px | USD | estimated | model text |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        result = r.get("file") or r.get("error", "")
        lines.append(
            f"| {r['product']} | {r['model']} | {r['variation']} | {result} "
            f"| {r.get('raw_px', '')} | {r.get('usd', '')} | {r.get('estimated', '')} "
            f"| {r.get('model_text', '')} |"
        )
    lines += ["", "## Response shape (first response, keys and types only)", "", "```json"]
    lines += [json.dumps(shape, indent=1), "```"]
    report = args.out / "docs" / "live-runs" / f"{stamp}-p0-10-images-{role}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
