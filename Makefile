# OpenMagpie Makefile
# Per-concern targets live in make/*.mk

include make/dev.mk

.PHONY: help
help:
	@./scripts/make-help.sh $(MAKEFILE_LIST)
