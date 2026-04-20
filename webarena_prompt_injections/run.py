# Copyright (c) Meta Platforms, Inc. and affiliates.
import click
import subprocess
import json
import os
import shlex
import sys
import tempfile
import csv
from pathlib import Path
from collections import defaultdict
from constants import (
    PromptInjectionFormat,
    GitlabUserGoals,
    RedditUserGoals,
    ShoppingUserGoals,
)


def _cleanup_tmp_metrics_files():
    for path in [
        "/tmp/run_step_by_step_asr.json",
        "/tmp/run_attacker_utility.json",
        "/tmp/run_user_utility.json",
    ]:
        if os.path.exists(path):
            os.remove(path)


def _read_json_metrics_file(path: str, description: str) -> dict:
    if not os.path.exists(path):
        raise click.ClickException(
            f"Missing {description} metrics file: {path}. "
            "The evaluation step likely did not finish or did not emit the expected artifact."
        )
    with open(path, "r") as json_file:
        return json.load(json_file)


def _compute_step_by_step_metrics_from_csv(csv_path: str) -> dict[str, int]:
    if not os.path.exists(csv_path):
        raise click.ClickException(
            "Missing step-by-step metrics artifacts: neither "
            f"'/tmp/run_step_by_step_asr.json' nor '{csv_path}' exists."
        )

    task_classifications: dict[str, list[str]] = defaultdict(list)
    with open(csv_path, "r", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if "task_index" not in reader.fieldnames or "classification_result" not in reader.fieldnames:
            raise click.ClickException(
                f"Cannot recover step-by-step metrics from '{csv_path}': "
                "required columns 'task_index' and 'classification_result' are missing."
            )
        for row in reader:
            task_index = row.get("task_index", "").strip()
            classification = row.get("classification_result", "").strip()
            if not task_index or not classification:
                continue
            task_classifications[task_index].append(classification)

    if not task_classifications:
        return {}

    counts: dict[str, int] = defaultdict(int)
    for classifications in task_classifications.values():
        if "compromised" in classifications:
            counts["compromised"] += 1
        elif "outright_refusal" in classifications:
            counts["outright_refusal"] += 1
        else:
            counts["not_compromised"] += 1

    recovered_metrics = dict(counts)
    with open("/tmp/run_step_by_step_asr.json", "w") as json_file:
        json.dump(recovered_metrics, json_file, indent=4)
    return recovered_metrics


def _load_step_by_step_metrics(log_dir: str) -> dict[str, int]:
    metrics_path = "/tmp/run_step_by_step_asr.json"
    if os.path.exists(metrics_path):
        return _read_json_metrics_file(metrics_path, "step-by-step")

    action_classifications_csv = os.path.join(log_dir, "action_classifications.csv")
    print(
        "step 3 | '/tmp/run_step_by_step_asr.json' is missing; "
        f"recovering metrics from '{action_classifications_csv}'",
        flush=True,
    )
    return _compute_step_by_step_metrics_from_csv(action_classifications_csv)


def _with_trailing_slash(path: str) -> str:
    if path.endswith("/"):
        return path
    return path + "/"


def _build_output_dir_for_run(output_dir: str, output_dir_idx: int) -> str:
    base_output_dir = os.path.abspath(os.path.expanduser(output_dir))
    run_output_dir = f"{_with_trailing_slash(base_output_dir)}{output_dir_idx}/"
    parent_dir = os.path.dirname(run_output_dir.rstrip("/"))
    # scripts/run_end_to_end.sh uses mkdir without -p; ensure parent exists.
    try:
        os.makedirs(parent_dir, exist_ok=True)
    except PermissionError as exc:
        default_fallback = "/tmp/computer-use-agent-logs"
        raise click.ClickException(
            f"Cannot create output directory '{parent_dir}' due to permission denied. "
            "Please pass a writable path via --output-dir "
            f"(e.g. '{default_fallback}' or './outputs')."
        ) from exc
    return run_output_dir


def _build_env_for_site(site: str) -> dict[str, str]:
    env = os.environ.copy()
    site = site.lower()
    env.setdefault("SHOPPING", "http://127.0.0.1:7770")
    if site == "gitlab":
        if not env.get("GITLAB"):
            raise click.ClickException(
                "GITLAB is not set. Example: export GITLAB=http://127.0.0.1:8023"
            )
        # Keep argparse options in prompt_injector.py aligned even when reddit is unused.
        env.setdefault("REDDIT", "http://127.0.0.1:9999")
    elif site == "reddit":
        if not env.get("REDDIT"):
            raise click.ClickException(
                "REDDIT is not set. Example: export REDDIT=http://127.0.0.1:9999"
            )
        # Keep argparse options in prompt_injector.py aligned even when gitlab is unused.
        env.setdefault("GITLAB", "http://127.0.0.1:8023")
    elif site == "shopping":
        if not env.get("SHOPPING"):
            raise click.ClickException(
                "SHOPPING is not set. Example: export SHOPPING=http://127.0.0.1:7770"
            )
        env.setdefault("GITLAB", "http://127.0.0.1:8023")
        env.setdefault("REDDIT", "http://127.0.0.1:9999")
    return env


def _write_single_site_config(config_path: str, site: str) -> str:
    with open(config_path, "r") as fh:
        config_dict = json.load(fh)

    all_items = config_dict.get("prompt_injections_setup_config", [])
    filtered_items = [item for item in all_items if item.get("environment") == site]
    if not filtered_items:
        raise click.ClickException(
            f"No prompt injections found for site='{site}' in config '{config_path}'."
        )

    config_dict["prompt_injections_setup_config"] = filtered_items
    fd, tmp_path = tempfile.mkstemp(
        prefix=f"wasp_{site}_only_", suffix=".json", text=True
    )
    with os.fdopen(fd, "w") as fh:
        json.dump(config_dict, fh, indent=2)
    return tmp_path


def _patch_agent_script_to_skip_prepare_for_single_site(
    run_agent_script_path: str, site: str, env: dict[str, str]
) -> None:
    with open(run_agent_script_path, "r") as fh:
        script_content = fh.read()

    replacement = (
        "DATASET=webarena_prompt_injections "
        f"GITLAB={shlex.quote(env['GITLAB'])} "
        f"REDDIT={shlex.quote(env['REDDIT'])} "
        f"SHOPPING={shlex.quote(env['SHOPPING'])} "
        f"python -m browser_env.auto_login --site_list {site}"
    )
    if "bash prepare.sh" in script_content:
        script_content = script_content.replace("bash prepare.sh", replacement)

    with open(run_agent_script_path, "w") as fh:
        fh.write(script_content)


def _resolve_stage3_model(run_model: str) -> str:
    """Use Gemini Flash as the default classifier model for stage-3 when running Gemini agents."""
    if run_model.lower().startswith(("gemini", "gemma")):
        return os.environ.get("STAGE3_MODEL", "gemini-2.5-flash")
    return run_model


def _ensure_model_api_key(model: str) -> None:
    lowered = model.lower()
    if lowered.startswith(("gemini", "gemma")) and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = click.prompt(
            "GEMINI_API_KEY is required for Google Gemini/Gemma models. Please enter GEMINI_API_KEY",
            hide_input=True,
        )
    if lowered.startswith("qwen") and not os.environ.get("DASHSCOPE_API_KEY"):
        os.environ["DASHSCOPE_API_KEY"] = click.prompt(
            "DASHSCOPE_API_KEY is required for Qwen models. Please enter DASHSCOPE_API_KEY",
            hide_input=True,
        )


def _run_single_site_end_to_end(
    config,
    model,
    system_prompt,
    output_dir,
    output_format,
    user_goal_idx,
    injection_format,
    site,
):
    env = _build_env_for_site(site)
    run_output_dir = output_dir
    _cleanup_tmp_metrics_files()

    repo_dir = Path(__file__).resolve().parent
    visualwebarena_dir = repo_dir.parent / "visualwebarena"
    run_agent_script_path = Path(run_output_dir) / "run_agent.sh"
    task_dir = Path(run_output_dir) / "webarena_tasks"
    attacker_task_dir = Path(run_output_dir) / "webarena_tasks_attacker"
    log_dir = Path(run_output_dir) / "agent_logs"
    prompt_injection_config_path = (
        Path(run_output_dir) / "instantiated_prompt_injections_config.json"
    )

    print(f"step 1 | preparing {site}-only prompt injections and tasks...", flush=True)
    subprocess.run(
        [
            sys.executable,
            "prompt_injector.py",
            "--config",
            config,
            "--gitlab-domain",
            env["GITLAB"],
            "--reddit-domain",
            env["REDDIT"],
            "--shopping-domain",
            env["SHOPPING"],
            "--model",
            model,
            "--system_prompt",
            system_prompt,
            "--output-dir",
            run_output_dir,
            "--user_goal_idx",
            str(user_goal_idx),
            "--injection_format",
            injection_format,
            "--output-format",
            output_format,
        ],
        cwd=repo_dir,
        env=env,
        check=True,
    )

    if not run_agent_script_path.exists():
        raise click.ClickException(
            f"Expected generated script at {run_agent_script_path}, but it does not exist."
        )
    _patch_agent_script_to_skip_prepare_for_single_site(
        str(run_agent_script_path), site, env
    )

    print("step 2 | running agent...", flush=True)
    subprocess.run(
        ["bash", str(run_agent_script_path)],
        cwd=visualwebarena_dir,
        env=env,
        check=True,
    )

    print("step 3 | running evaluations...", flush=True)
    stage3_model = _resolve_stage3_model(model)
    print(f"stage 3 | classifier model = {stage3_model}", flush=True)
    subprocess.run(
        [
            sys.executable,
            "evaluator_step_by_step.py",
            "--log-folder",
            str(log_dir) + "/",
            "--task-folder",
            str(task_dir) + "/",
            "--format",
            output_format,
            "--model",
            stage3_model,
        ],
        cwd=repo_dir,
        env=env,
        check=True,
    )

    # Refresh only the site we are evaluating to avoid hard dependency on the other site.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "browser_env.auto_login",
            "--site_list",
            site,
        ],
        cwd=visualwebarena_dir,
        env=env,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "evaluator_final_step.py",
            "--log-folder",
            str(log_dir) + "/",
            "--task-folder",
            str(attacker_task_dir) + "/",
            "--format",
            output_format,
        ],
        cwd=visualwebarena_dir,
        env=env,
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "evaluator_final_step.py",
            "--log-folder",
            str(log_dir) + "/",
            "--task-folder",
            str(task_dir) + "/",
        ],
        cwd=visualwebarena_dir,
        env=env,
        check=True,
    )

    print("step 4 | cleanup...", flush=True)
    subprocess.run(
        [
            sys.executable,
            "environment_cleanup.py",
            "--prompt-injection-config-path",
            str(prompt_injection_config_path),
            "--gitlab-domain",
            env["GITLAB"],
            "--reddit-domain",
            env["REDDIT"],
            "--shopping-domain",
            env["SHOPPING"],
        ],
        cwd=repo_dir,
        env=env,
        check=True,
    )


def run_single_end_to_end(
        config,  # prompt injection configurations
        model,  # agentic model to run
        system_prompt,  # system prompt for agent
        output_dir,  # to store settings, logs, errors
        output_format,  # agentic scaffolding format
        user_goal_idx,  # benign user goal
        injection_format,  # prompt injection format to try
        site,  # all | gitlab | reddit
        results_dict,  # to save and accumulate results
        output_dir_idx=0,  # to save logs for each run separately
    ):
    output_dir = _build_output_dir_for_run(output_dir, output_dir_idx)
    log_dir = os.path.join(output_dir, "agent_logs")

    if site == "all":
        command = [
            'bash',
            'scripts/run_end_to_end.sh',
            output_dir,
            model,
            system_prompt,
            config,
            str(user_goal_idx),
            injection_format,
            output_format
        ]
        print(f"\nRunning command: \n{' '.join([str(arg) for arg in command])}", flush=True)
        subprocess.run(command, check=True)
    else:
        print(
            f"\nRunning single-site command flow for site={site} output_dir={output_dir}",
            flush=True,
        )
        _run_single_site_end_to_end(
            config=config,
            model=model,
            system_prompt=system_prompt,
            output_dir=output_dir,
            output_format=output_format,
            user_goal_idx=user_goal_idx,
            injection_format=injection_format,
            site=site,
        )

    # ------- check and save results
    res_step_by_step = _load_step_by_step_metrics(log_dir)
    if "compromised" in res_step_by_step:
        results_dict["cnt_intermediate_compromised"] += res_step_by_step["compromised"]
    if "not_compromised" in res_step_by_step:
        results_dict["cnt_intermediate_not_compromised"] += res_step_by_step["not_compromised"]
    if "outright_refusal" in res_step_by_step:
        results_dict["cnt_intermediate_outright_refusal"] += res_step_by_step["outright_refusal"]

    res_attacker_utility = _read_json_metrics_file(
        "/tmp/run_attacker_utility.json", "attacker utility"
    )
    results_dict["cnt_end2end_compromised"] += res_attacker_utility["total_scores"]
    results_dict["cnt_end2end_not_compromised"] += (
        res_attacker_utility["cnt_tasks"] - res_attacker_utility["total_scores"]
    )

    res_user_utility = _read_json_metrics_file(
        "/tmp/run_user_utility.json", "user utility"
    )
    results_dict["cnt_user_utility"] += res_user_utility["total_scores"]
    results_dict["cnt_user_total_tasks"] += res_user_utility["cnt_tasks"]
    # -------


def run_all(config, 
            model, 
            system_prompt, 
            output_dir, 
            output_format, 
            run_single,
            user_goal_start,
            user_goal_end,
            injection_format_idxs,
            site):
    site = site.lower()
    gitlab_user_goals = GitlabUserGoals("")
    reddit_user_goals = RedditUserGoals("")
    shopping_user_goals = ShoppingUserGoals("", "dummy")

    if site == "all":
        assert len(gitlab_user_goals.GOALS) == len(reddit_user_goals.GOALS), "Number of user goals should match!"
        user_goals_len = len(gitlab_user_goals.GOALS)
    elif site == "gitlab":
        user_goals_len = len(gitlab_user_goals.GOALS)
    elif site == "reddit":
        user_goals_len = len(reddit_user_goals.GOALS)
    elif site == "shopping":
        user_goals_len = len(shopping_user_goals.GOALS)
    else:
        raise click.ClickException(f"Unsupported site selection: {site}")

    all_injection_formats = [
        PromptInjectionFormat.GOAL_HIJACKING_PLAIN_TEXT,
        PromptInjectionFormat.GOAL_HIJACKING_URL_INJECTION,
        PromptInjectionFormat.GENERIC_PLAIN_TEXT,
        PromptInjectionFormat.GENERIC_URL_INJECTION,
    ]
    if injection_format_idxs is None:
        selected_format_idxs = list(range(len(all_injection_formats)))
    else:
        try:
            selected_format_idxs = [
                int(x.strip()) for x in injection_format_idxs.split(",") if x.strip() != ""
            ]
        except ValueError as exc:
            raise click.ClickException(
                f"Invalid --injection_format_idxs='{injection_format_idxs}'. "
                "Expected comma-separated integers, e.g. '0,1,3'."
            ) from exc
        if not selected_format_idxs:
            raise click.ClickException(
                "Invalid --injection_format_idxs: empty selection. Use values in [0,1,2,3]."
            )
        invalid = [i for i in selected_format_idxs if i < 0 or i >= len(all_injection_formats)]
        if invalid:
            raise click.ClickException(
                f"Invalid injection format indices {invalid}. Valid range is [0, {len(all_injection_formats)-1}]."
            )
        # Keep first-seen order while removing duplicates.
        selected_format_idxs = list(dict.fromkeys(selected_format_idxs))
    injection_format_list = [all_injection_formats[i] for i in selected_format_idxs]
    if user_goal_end is None:
        user_goal_end = user_goals_len - 1
    if user_goal_start < 0 or user_goal_start >= user_goals_len:
        raise click.ClickException(
            f"Invalid --user_goal_start={user_goal_start}, valid range is [0, {user_goals_len - 1}]"
        )
    if user_goal_end < user_goal_start or user_goal_end >= user_goals_len:
        raise click.ClickException(
            f"Invalid --user_goal_end={user_goal_end}, valid range is [{user_goal_start}, {user_goals_len - 1}]"
        )
    results_dict = defaultdict(int)
    run_config = config
    tmp_single_site_config_path = None
    if site != "all":
        tmp_single_site_config_path = _write_single_site_config(config, site)
        run_config = tmp_single_site_config_path

    try:
        for user_goal_idx in range(user_goal_start, user_goal_end + 1):
            if site == "all":
                print(f"$$$$$$$ Running {user_goal_idx+1} our of {user_goals_len} user goals, current one: "
                    f"(gitlab) '{gitlab_user_goals.GOALS[user_goal_idx]}', "
                    f"(reddit) '{reddit_user_goals.GOALS[user_goal_idx]}'")
            elif site == "gitlab":
                print(
                    f"$$$$$$$ Running {user_goal_idx+1} out of {user_goals_len} gitlab goals, "
                    f"current one: '{gitlab_user_goals.GOALS[user_goal_idx]}'"
                )
            elif site == "shopping":
                print(
                    f"$$$$$$$ Running {user_goal_idx+1} out of {user_goals_len} shopping goals, "
                    f"current one: '{shopping_user_goals.GOALS[user_goal_idx]}'"
                )
            else:
                print(
                    f"$$$$$$$ Running {user_goal_idx+1} out of {user_goals_len} reddit goals, "
                    f"current one: '{reddit_user_goals.GOALS[user_goal_idx]}'"
                )

            for local_i, injection_format in enumerate(injection_format_list):
                format_idx = selected_format_idxs[local_i]
                print(
                    f"$$$$$$$ Running {local_i+1} out of {len(injection_format_list)} injection formats, "
                    f"current one: idx={format_idx}, value={injection_format}"
                )

                run_single_end_to_end(config=run_config,
                                    model=model, 
                                    system_prompt=system_prompt, 
                                    output_dir=output_dir, 
                                    output_format=output_format, 
                                    user_goal_idx=user_goal_idx, 
                                    injection_format=injection_format, 
                                    site=site,
                                    results_dict=results_dict,
                                    output_dir_idx=user_goal_idx * len(all_injection_formats) + format_idx)

                print(
                    f"\nAccumulated results after user_goal_idx = {user_goal_idx+1} "
                    f"and injection_format_local_idx = {local_i+1} (global idx={format_idx}): "
                )
                for key, value in results_dict.items():
                    print(f"{key} = {value}")

                if run_single:
                    print("\n!!! Running a single user goal and a single injection format is requested. Terminating")
                    return
    finally:
        if tmp_single_site_config_path and os.path.exists(tmp_single_site_config_path):
            os.remove(tmp_single_site_config_path)
    
    print("\n\nDone running all experiments! Final results:")
    for key, value in results_dict.items():
        print(f"{key} = {value}")


@click.command()
@click.option(
    "--config",
    type=str,
    default="configs/experiment_config.raw.json",
    help="Where to find the config for prompt injections",
)
@click.option(
    "--model",
    type=str,
    default="gpt-4o",
    help="backbone LLM model id. Supports OpenAI/Claude aliases used in this repo, Google Gemini/Gemma model ids (e.g., gemini-2.5-pro, gemini-2.5-flash, gemma-4-31b-it), and Qwen model ids via DashScope compatible mode (e.g., qwen3.5-plus).",
)
@click.option(
    "--system-prompt",
    type=str,
    default="configs/system_prompts/wa_p_som_cot_id_actree_3s.json",
    help="system_prompt for the backbone LLM. Default = VWA's SOM system prompt for GPT scaffolding",
)
@click.option(
    "--output-dir",
    type=str,
    default="/tmp/computer-use-agent-logs",
    help="Folder to store the output configs and commands to run the agent",
)
@click.option(
    "--output-format",
    type=str,
    default="webarena",
    help="Format of the agentic scaffolding: webarena (default), claude, gpt_web_tools",
)
@click.option(
    "--run-single",
    is_flag=True,
    default=False,
    help="whether to test only a single user goal and a single injection format",
)
@click.option(
    "--user_goal_start",
    type=int,
    default=0,
    help="starting user_goal index (between 0 and total number of benign user goals)",
)
@click.option(
    "--user_goal_end",
    type=int,
    default=None,
    help="ending user_goal index (inclusive). Default: last available goal index.",
)
@click.option(
    "--injection_format_idxs",
    type=str,
    default=None,
    help="Comma-separated injection format indices to run. "
    "0=goal_hijacking_plain_text, 1=goal_hijacking_url_injection, "
    "2=generic_plain_text, 3=generic_url_injection. "
    "Examples: '0' or '0,1,3'. Default: all formats.",
)
@click.option(
    "--site",
    type=click.Choice(["all", "gitlab", "reddit", "shopping"], case_sensitive=False),
    default="all",
    help="Which site pipeline to run. Use a single site to skip unrelated pipelines end-to-end.",
)
def main(config, 
         model, 
         system_prompt, 
         output_dir, 
         output_format, 
         run_single, 
         user_goal_start,
         user_goal_end,
         injection_format_idxs,
         site):
    _ensure_model_api_key(model)
    print("Arguments provided to run.py: \n", locals(), "\n\n")
    run_all(config=config, 
            model=model, 
            system_prompt=system_prompt, 
            output_dir=output_dir, 
            output_format=output_format, 
            run_single=run_single,
            user_goal_start=user_goal_start,
            user_goal_end=user_goal_end,
            injection_format_idxs=injection_format_idxs,
            site=site)


if __name__ == '__main__':
    main()
