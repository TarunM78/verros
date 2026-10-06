# Upstream

This directory is a vendored copy of https://github.com/CKraft11/pygeartrain
at commit `46077ceba5885734f30e85e366a5cbd7ef6ce5ef` (2025-04-18), plus the changes listed in the repository README.

To diff against upstream:

    git clone https://github.com/CKraft11/pygeartrain /tmp/pygeartrain-upstream
    git -C /tmp/pygeartrain-upstream checkout 46077ceba5885734f30e85e366a5cbd7ef6ce5ef
    diff -r --exclude=.venv --exclude=__pycache__ /tmp/pygeartrain-upstream pygeartrain
