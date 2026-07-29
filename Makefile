# Backend hardening: reproducible test entry points.
#
# `make test` runs the backend suite in the already-active environment.
# `make test-clean` builds a throwaway, isolated micromamba environment from the
# pinned lock file and runs the suite there, so a green result does not depend on
# whatever happens to be installed in the day-to-day env.

ENV_NAME ?= doe-backend-test
PY_VERSION ?= 3.11.15

.PHONY: test test-clean clean-env

test:
	python -m pytest

test-clean:
	micromamba create -y -n $(ENV_NAME) -c conda-forge python=$(PY_VERSION)
	micromamba run -n $(ENV_NAME) python -m pip install --no-deps -r requirements.lock
	micromamba run -n $(ENV_NAME) python -m pytest
	micromamba env remove -y -n $(ENV_NAME)

clean-env:
	micromamba env remove -y -n $(ENV_NAME) || true
