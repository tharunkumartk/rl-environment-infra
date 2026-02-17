a system for evaluating browser-based ai agents on data retrieval tasks. the agent navigates a metabase analytics dashboard inside an isolated docker environment, executes natural language queries, and returns structured json answers that are automatically verified against ground-truth solutions.

## overview

the project is split into two main components:

- **`rl-env-dashboard/`** — a full-stack web dashboard for orchestrating and monitoring agent rollouts
- **`computer-use-preview/`** — a gemini-powered browser agent that interacts with web uis using playwright
- **`tasks.json`** — a set of 10 benchmark tasks querying product, order, and account data
- **`metabase_envdata.sql`** — the sql dataset loaded into each isolated rollout environment

## how it works

1. tasks are uploaded via the dashboard as a `tasks.json` file
2. for each rollout, a dedicated postgres container is spun up with the sql dataset loaded
3. a metabase instance is connected to that postgres container on a unique port
4. the gemini computer use agent navigates the metabase ui using playwright to answer the task
5. the agent's json output is compared against the expected answer
6. containers are torn down and results (including a video recording) are stored

## components

### rl-env-dashboard

a fastapi + next.js application for managing the full rollout lifecycle.

- **backend**: fastapi server with sqlite database, docker container orchestration, and a thread pool for parallel rollouts
- **frontend**: next.js dashboard for uploading tasks, spawning rollouts, and monitoring results in real time

see [`rl-env-dashboard/README.md`](rl-env-dashboard/README.md) for setup and usage instructions.

### computer-use-preview

a browser automation agent powered by google gemini's computer use capability.

- uses `gemini-2.5-computer-use-preview` to interpret screenshots and issue browser actions
- supports local playwright and browserbase environments
- logs screenshots and step-by-step traces per rollout

see [`computer-use-preview/README.md`](computer-use-preview/README.md) for setup and usage instructions.

## tasks

the benchmark consists of 10 sql-style data retrieval problems over a synthetic e-commerce dataset (products, orders, accounts, reviews, invoices). example:

```json
{
  "id": "problem1",
  "task": "retrieve product titles and ratings for products with ratings above '4.5' and price less than '40'. return in the format {product_titles: list[str], ratings: list[number]}.",
  "answer": "{\"product_titles\": [\"Rustic Paper Wallet\", \"Ergonomic Wool Bag\", \"Lightweight Linen Hat\"], \"ratings\": [4.6, 5, 5]}"
}
```

## prerequisites

- python 3.10+
- node.js 18+
- docker
- playwright with chromium (`playwright install chrome`)
- a gemini api key (`GEMINI_API_KEY`)

## quick start

```bash
# 1. start the backend
cd rl-env-dashboard/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r ../computer-use-preview/requirements.txt
export GEMINI_API_KEY="your-key"
uvicorn main:app --reload

# 2. start the frontend (in a separate terminal)
cd rl-env-dashboard/frontend
npm install
npm run dev
```

open `http://localhost:3000`, upload `tasks.json`, and start spawning rollouts.
