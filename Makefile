.PHONY: build check fmt test vet help
.DEFAULT_GOAL := help
GOVERSION := $(shell go version | cut -d ' ' -f 3 | cut -d '.' -f 2)
SHELL := /usr/bin/env bash
ROOT := $(shell pwd)
TASKS := $(shell ls ${ROOT}/task)
HOOKS := $(shell ls ${ROOT}/hook)

build: test build-gorelease ## build all
	@echo " > build finished"

build-gorelease: ## build everything with goreleaser
	@echo " > building binaries"
	goreleaser --snapshot --rm-dist

install: ## install plugin to optimus directory
	mkdir -p ~/.optimus/plugins
	cp ./dist/bq2bq_darwin_amd64/* ~/.optimus/plugins/

clean: ## clean binaries
	rm -rf ./dist

help:
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'