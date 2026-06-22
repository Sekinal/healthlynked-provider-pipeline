"""Entry point — delegates to the pipeline CLI.

Run the directory-update funnel:
    uv run python main.py --seed data/seed_npis.json
    uv run python main.py --npi 1962515924 --enrich --reconcile
or equivalently `uv run python -m pipeline.run ...`.
"""

from pipeline.run import main

if __name__ == "__main__":
    main()
