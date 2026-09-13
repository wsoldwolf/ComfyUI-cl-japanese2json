"""Manual real-GGUF compiler check; never queues video generation.

Run from the repository using ComfyUI's Python environment:
  python tools/check_compiler_translation.py --model MODEL.gguf --source INPUT.md \
      --output-dir generated/compiler-check --seeds 1 17 42

Read canonical English and Plan output to assess meaning. Structural success
and a negation marker are not proof of semantic equivalence.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
from pathlib import Path
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 17, 42])
    parser.add_argument("--n-ctx", type=int, default=16384)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--retry-max", type=int, default=2)
    parser.add_argument("--batches", type=int, nargs="+",
                        help="Check selected 1-based batches without compiling a full Plan")
    args = parser.parse_args()
    if args.retry_max < 0:
        parser.error("manual checks require a finite --retry-max")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))

    def module(name):
        return importlib.import_module(f"{root.name}.{name}")

    backend = module("common.gguf.runtime").LlamaBackend()
    compiler = module("node_japanese_to_json.compiler.llmj2e")
    mdparse = module("node_japanese_to_json.compiler.mdparse")
    jsongen = module("node_japanese_to_json.compiler.jsongen")
    prompt = module("node_japanese_to_json.compiler.system_prompt").load_system_prompt()
    source = args.source.read_text(encoding="utf-8-sig")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental overwriting of an earlier review.
    with (args.output_dir / "source.md").open("x", encoding="utf-8") as handle:
        handle.write(source)
    (args.output_dir / "system_prompt.txt").write_text(prompt, encoding="utf-8")
    (args.output_dir / "settings.json").write_text(
        json.dumps({key: str(value) if isinstance(value, Path) else value
                    for key, value in vars(args).items()}, indent=2), encoding="utf-8"
    )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        backend.ensure_loaded(
            args.model, n_ctx=args.n_ctx, gpu_layers=-1, n_batch=256,
            flash_attn=True, kv_cache_type="q8_0", op_offload=True,
        )
        for seed in args.seeds:
            events = []
            started = time.monotonic()
            try:
                if args.batches:
                    document = compiler.lex_japanese_markdown(source)
                    records, _ = compiler._sentence_records(document.records)
                    batches = compiler._make_batches(records, backend, prompt, args.max_tokens)
                    selected = {}
                    for number in args.batches:
                        if not 1 <= number <= len(batches):
                            raise ValueError(f"Batch {number} outside 1..{len(batches)}")
                        selected[number] = compiler._translate_batch(
                            batches[number - 1], backend, prompt,
                            max_tokens=args.max_tokens, temperature=0.1, top_p=0.9,
                            repetition_penalty=1.05, seed=seed, retry_max=args.retry_max,
                            batch_index=number - 1, batch_count=len(batches),
                            debug_events=events, progress_callback=None, interrupt_callback=None,
                        )
                    (args.output_dir / f"seed_{seed}_batches.json").write_text(
                        json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    continue
                canonical = compiler.translate_markdown(
                    source, backend, prompt, seed=seed, retry_max=args.retry_max,
                    max_tokens=args.max_tokens, debug_events=events,
                )
                plan = jsongen.generate_json(
                    mdparse.parse_markdown(canonical), speech_guard="warn"
                )
                (args.output_dir / f"seed_{seed}_canonical.md").write_text(
                    canonical, encoding="utf-8"
                )
                (args.output_dir / f"seed_{seed}_plan.json").write_text(
                    plan, encoding="utf-8"
                )
                logging.info("seed=%d completed in %.1fs; inspect meaning in %s",
                             seed, time.monotonic() - started, args.output_dir)
            finally:
                (args.output_dir / f"seed_{seed}_events.json").write_text(
                    json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    finally:
        backend.clear_model()


if __name__ == "__main__":
    main()
