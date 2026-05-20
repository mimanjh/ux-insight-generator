"""
Consistency eval: run the analyzer N times on the same screenshot
and report how stable the output is across runs.

Usage:
    python eval_consistency.py test_screenshots/amazon_product.png
    python eval_consistency.py test_screenshots/amazon_product.png --runs 5

Output:
    - Each run's JSON saved to runs/eval/<image_stem>/run_<n>.json
    - Console summary of finding counts, severity distribution, and which
      finding titles appeared in which runs.

This is a simple structural/lexical comparison — it doesn't try to judge
whether two slightly differently-worded findings are "the same idea."
That's the next step up (semantic eval), and we're deferring it.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from analyze_screenshot import analyze_screenshot

EVAL_DIR = Path("runs/eval")


def run_n_times(image_path: str, n: int) -> list[dict]:
    """Call the analyzer n times sequentially and return all results."""
    results = []
    for i in range(1, n + 1):
        print(f"  Run {i}/{n}...", flush=True)
        results.append(analyze_screenshot(image_path))
    return results


def save_runs(results: list[dict], image_stem: str) -> Path:
    """Save each run's JSON under runs/eval/<image_stem>/."""
    out_dir = EVAL_DIR / image_stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, result in enumerate(results, start=1):
        path = out_dir / f"run_{i}.json"
        path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return out_dir


def summarize(results: list[dict]) -> None:
    """Print a console summary of consistency across runs."""
    n = len(results)

    print()
    print("=" * 60)
    print(f"Consistency summary across {n} runs")
    print("=" * 60)

    # Finding counts per run
    counts = [len(r["findings"]) for r in results]
    print(f"\nFindings per run: {counts}")
    print(f"  min/max/avg: {min(counts)}/{max(counts)}/{sum(counts)/n:.1f}")

    # Severity distribution per run
    print("\nSeverity distribution per run:")
    for i, r in enumerate(results, start=1):
        sev = Counter(f["severity"] for f in r["findings"])
        line = ", ".join(f"{k}={sev[k]}" for k in ["high", "medium", "low"] if sev[k])
        print(f"  Run {i}: {line or '(none)'}")

    # Theme stability: which themes appear in which runs, and how often
    # each appears in each run (themes can repeat within a run).
    theme_to_runs: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(results, start=1):
        for f in r["findings"]:
            theme_to_runs[f["theme"]].append(i)

    print(f"\nTheme stability (themes can repeat within a run):")
    print("(STABLE = appears in every run; flaky = appears in some)\n")

    sorted_themes = sorted(
        theme_to_runs.items(),
        key=lambda kv: (-len(set(kv[1])), kv[0]),
    )
    for theme, runs in sorted_themes:
        unique_runs = sorted(set(runs))
        marker = "[STABLE]" if len(unique_runs) == n else f"[{len(unique_runs)}/{n}]"
        print(f"  {marker} {theme} (total occurrences: {len(runs)})")
        print(f"           seen in runs: {unique_runs}")

    # Titles per theme — helpful for spotting whether the model is picking
    # the same theme for the same underlying issue across runs.
    print("\nTitles grouped by theme:")
    titles_by_theme: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for i, r in enumerate(results, start=1):
        for f in r["findings"]:
            titles_by_theme[f["theme"]].append((i, f["title"]))
    for theme in sorted(titles_by_theme):
        print(f"\n  {theme}:")
        for run_idx, title in titles_by_theme[theme]:
            print(f"    [run {run_idx}] {title}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run analyzer N times on one screenshot and report consistency."
    )
    parser.add_argument("image_path", help="Path to the screenshot image")
    parser.add_argument(
        "--runs", type=int, default=3, help="Number of repeat runs (default: 3)"
    )
    args = parser.parse_args()

    image_stem = Path(args.image_path).stem
    print(f"Running {args.runs} analyses on {args.image_path}...")
    results = run_n_times(args.image_path, args.runs)

    out_dir = save_runs(results, image_stem)
    print(f"\nSaved {args.runs} runs to {out_dir}/")

    summarize(results)
