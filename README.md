
This the open source code from group LLMao. 

The project title: Mining Instruction Patterns that Trigger Unsafe Behavior in Autonomous Web Agents

## Prerequisites

- Docker
- Python `3.10` available as `python3.10` or set `PYTHON_BIN`
- Playwright system dependencies if `playwright install` asks for them

## Python environment setup

From the repo root:

```bash
bash webarena_prompt_injections/setup.sh
```

This creates local environments at:

- `visualwebarena/venv`
- `webarena_prompt_injections/venv`

Set the shared runtime variables before running any site:

```bash
export DATASET=webarena_prompt_injections
export GEMINI_API_KEY="<your_gemini_api_key>"
```

You can also use `.env.example` as a starting point.

## Prompt Template Location

Prompt-injection templates live in:

- `webarena_prompt_injections/constants.py`

Specifically, edit `PromptInjectionFormat.MESSAGE` if you want to change the prompt template strings used by the benchmark.

## GitLab

### Download the GitLab environment image

Official WebArena mirrors:

- Google Drive: <https://drive.google.com/file/d/19W8qM0DPyRvWCLyQe0qtnCWAHGruolMR/view?usp=sharing>
- Internet Archive: <https://archive.org/download/webarena-env-gitlab-image>
- Direct tar: <http://metis.lti.cs.cmu.edu/webarena-images/gitlab-populated-final-port8023.tar>

Load the image:

```bash
docker load --input gitlab-populated-final-port8023.tar
```

### Create the GitLab environment

For local runs on one machine:

```bash
GITLAB_BASE_URL=http://127.0.0.1:8023 bash visualwebarena/scripts/reset_gitlab.sh
```

For a remote host, replace `127.0.0.1` with your server hostname or public IP.

### Run WASP on GitLab

```bash
export GITLAB=http://127.0.0.1:8023

cd webarena_prompt_injections
source venv/bin/activate

python run.py \
  --config configs/experiment_config.raw.json \
  --model gemini-3.1-pro-preview \
  --system-prompt configs/system_prompts/wa_p_som_cot_id_actree_3s.json \
  --output-dir ./output/gitlab \
  --output-format webarena \
  --user_goal_start 0 \
  --user_goal_end 0 \
  --injection_format_idxs 0,1,2,3 \
  --site gitlab
```

`run.py` will handle site-specific auto-login during execution.

### Reset the GitLab dataset

```bash
GITLAB_BASE_URL=http://127.0.0.1:8023 bash visualwebarena/scripts/reset_gitlab.sh
```

GitLab takes the longest to boot. Expect about 5 minutes for a full reset.

## Reddit

### Download the Reddit environment image

Official WebArena mirrors:

- Google Drive: <https://drive.google.com/file/d/17Qpp1iu_mPqzgO_73Z9BnFjHrzmX9DGf/view?usp=sharing>
- Internet Archive: <https://archive.org/download/webarena-env-forum-image>
- Direct tar: <http://metis.lti.cs.cmu.edu/webarena-images/postmill-populated-exposed-withimg.tar>

Load the image:

```bash
docker load --input postmill-populated-exposed-withimg.tar
```

### Create the Reddit environment

```bash
bash visualwebarena/scripts/reset_reddit.sh
```

This starts the forum container on `http://127.0.0.1:9999`.

### Run WASP on Reddit

```bash
export REDDIT=http://127.0.0.1:9999

cd webarena_prompt_injections
source venv/bin/activate

python run.py \
  --config configs/experiment_config.raw.json \
  --model gemini-3.1-pro-preview \
  --system-prompt configs/system_prompts/wa_p_som_cot_id_actree_3s.json \
  --output-dir ./output/reddit \
  --output-format webarena \
  --user_goal_start 0 \
  --user_goal_end 0 \
  --injection_format_idxs 0,1,2,3 \
  --site reddit
```

### Reset the Reddit dataset

```bash
bash visualwebarena/scripts/reset_reddit.sh
```

## Shopping

### Download the Shopping environment image

Official WebArena mirrors:

- Google Drive: <https://drive.google.com/file/d/1gxXalk9O0p9eu1YkIJcmZta1nvvyAJpA/view?usp=sharing>
- Internet Archive: <https://archive.org/download/webarena-env-shopping-image>
- Direct tar: <http://metis.lti.cs.cmu.edu/webarena-images/shopping_final_0712.tar>

Load the image:

```bash
docker load --input shopping_final_0712.tar
```

### Create the Shopping environment

```bash
SHOPPING_BASE_URL=http://127.0.0.1:7770 bash visualwebarena/scripts/reset_shopping.sh
```

For a remote host, replace `127.0.0.1` with your server hostname or public IP.

### Run WASP on Shopping

```bash
export SHOPPING=http://127.0.0.1:7770

cd webarena_prompt_injections
source venv/bin/activate

python run.py \
  --config configs/experiment_config.raw.json \
  --model gemini-3.1-pro-preview \
  --system-prompt configs/system_prompts/wa_p_som_cot_id_actree_3s.json \
  --output-dir ./output/shopping \
  --output-format webarena \
  --user_goal_start 0 \
  --user_goal_end 0 \
  --injection_format_idxs 0,1,2,3 \
  --site shopping
```

### Reset the Shopping dataset

```bash
SHOPPING_BASE_URL=http://127.0.0.1:7770 bash visualwebarena/scripts/reset_shopping.sh
```

## Notes

- The single-site flow is the recommended release workflow. Use `--site gitlab`, `--site reddit`, or `--site shopping`.
- The prompt-injection configs are stored in `webarena_prompt_injections/configs/experiment_config.raw.json`.
- The official environment download links above come from the WebArena environment setup documentation:
  <https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md>
