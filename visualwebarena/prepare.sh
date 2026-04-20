#!/bin/bash
# re-validate login information
mkdir -p ./.auth
python -m browser_env.auto_login
