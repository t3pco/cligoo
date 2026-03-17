SHELL := /bin/bash

# ── Development venv (project folder — for tests, lint, editable dev) ────────
VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

# ── Standalone install venv (survives moving/deleting the project folder) ─────
INSTALL_DIR  := $(HOME)/.local/share/cligoo
INSTALL_VENV := $(INSTALL_DIR)/venv
INSTALL_PIP  := $(INSTALL_VENV)/bin/pip

# ── ~/.local/bin target ───────────────────────────────────────────────────────
PREFIX       ?= $(HOME)/.local
BINDIR       ?= $(PREFIX)/bin
INSTALL_NAME ?= cligoo
INSTALL_PATH ?= $(BINDIR)/$(INSTALL_NAME)

CONFIG_SAMPLE ?= cligoo.toml.example
CONFIG_DIR    ?= $(HOME)/.config/cligoo
CONFIG_DEST   ?= $(CONFIG_DIR)/config.toml
TOKEN_DIR     ?= $(CONFIG_DIR)

.DEFAULT_GOAL := help

.PHONY: help check-deps venv _install-venv install install-dev uninstall-dev \
        install-browser install-config reconfigure uninstall reinstall lint fmt test run clean

help: ## Show available targets
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  %-20s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ─────────────────────────────────────────────────────────────────────────────
check-deps: ## Verify required system prerequisites
	@echo "Checking prerequisites..."
	@command -v python3 >/dev/null 2>&1 \
		|| { echo "✗ python3 not found"; exit 1; }
	@python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" \
		|| { echo "✗ Python 3.9+ required (found $$(python3 --version 2>&1))"; exit 1; }
	@echo "✓ python3 $$(python3 --version 2>&1 | awk '{print $$2}')"
	@mkdir -p "$(BINDIR)"
	@echo "✓ $(BINDIR)"
	@if echo "$$PATH" | tr ':' '\n' | grep -Fxq "$(BINDIR)"; then \
		echo "✓ $(BINDIR) is on PATH"; \
	else \
		echo "⚠ $(BINDIR) is not on PATH"; \
		echo "  Add this to your shell profile:"; \
		echo "  export PATH=\"$(BINDIR):\$$PATH\""; \
	fi

# ── Dev venv (tests / lint) ───────────────────────────────────────────────────
venv: ## Create (or repair) the development virtual environment
	@if ! "$(PIP)" --version >/dev/null 2>&1; then \
		echo "Creating dev virtual environment..."; \
		rm -rf $(VENV); \
		python3 -m venv $(VENV); \
		$(PIP) install --upgrade pip --quiet; \
		echo "✓ Dev virtual environment created"; \
	else \
		echo "✓ Dev virtual environment OK"; \
	fi

# ── Standalone install venv ───────────────────────────────────────────────────
_install-venv: ## (internal) Create or repair the standalone install venv
	@if ! "$(INSTALL_PIP)" --version >/dev/null 2>&1; then \
		echo "Creating standalone install environment..."; \
		rm -rf "$(INSTALL_VENV)"; \
		mkdir -p "$(INSTALL_DIR)"; \
		python3 -m venv "$(INSTALL_VENV)"; \
		"$(INSTALL_PIP)" install --upgrade pip --quiet; \
		echo "✓ Standalone environment created at $(INSTALL_VENV)"; \
	else \
		echo "✓ Standalone install environment OK"; \
	fi

# ── User-facing install targets ───────────────────────────────────────────────
install: check-deps _install-venv install-config ## Install cligoo as a standalone tool (project folder not needed afterwards)
	@echo "Installing cligoo..."
	@"$(INSTALL_PIP)" install . --quiet
	@mkdir -p "$(BINDIR)"
	@ln -sf "$(INSTALL_VENV)/bin/$(INSTALL_NAME)" "$(INSTALL_PATH)"
	@echo ""
	@echo "✓ Installed to $(INSTALL_PATH)  (standalone — project folder no longer needed)"
	@echo "  Run: $(INSTALL_NAME) --help"
	@echo "  Config: $(CONFIG_DEST)"

install-dev: check-deps venv ## Editable dev install — code changes take effect immediately, symlink points to .venv
	@$(PIP) install -e ".[dev]" --quiet
	@mkdir -p "$(BINDIR)"
	@ln -sf "$(CURDIR)/$(VENV)/bin/$(INSTALL_NAME)" "$(INSTALL_PATH)"
	@echo "✓ Dev install active: edits to src/ take effect immediately"
	@echo "  Run 'make install' to switch back to the standalone install"

uninstall-dev: ## Remove the dev-install symlink (keeps .venv for tests/lint); switches to standalone install if present
	@if [ -L "$(INSTALL_PATH)" ] && \
	   [ "$$(readlink "$(INSTALL_PATH)")" = "$(CURDIR)/$(VENV)/bin/$(INSTALL_NAME)" ]; then \
		rm -f "$(INSTALL_PATH)"; \
		echo "✓ Removed dev symlink $(INSTALL_PATH)"; \
		if [ -x "$(INSTALL_VENV)/bin/$(INSTALL_NAME)" ]; then \
			ln -sf "$(INSTALL_VENV)/bin/$(INSTALL_NAME)" "$(INSTALL_PATH)"; \
			echo "✓ Switched back to standalone install at $(INSTALL_PATH)"; \
		else \
			echo "  No standalone install found — run 'make install' to install one"; \
		fi; \
	else \
		echo "⚠ $(INSTALL_PATH) is not the dev symlink — nothing to do"; \
	fi

install-browser: check-deps _install-venv ## Install Playwright browser support into the standalone install venv
	@"$(INSTALL_PIP)" install ".[browser]" --quiet
	@if [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then \
		echo "✓ Playwright installed (will use system Google Chrome)"; \
	elif [ -x "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ]; then \
		echo "✓ Playwright installed (will use system Microsoft Edge)"; \
	else \
		echo "⚠ No system Chrome/Edge found — downloading Chromium (~130 MB)..."; \
		"$(INSTALL_VENV)/bin/playwright" install chromium; \
		echo "✓ Playwright + Chromium installed"; \
	fi
	@echo "  Run: cligoo login --browser"

install-config: ## Seed user config from template (interactive, only if missing)
	@mkdir -p "$(CONFIG_DIR)"
	@if [ ! -f "$(CONFIG_DEST)" ]; then \
		echo ""; \
		echo "── Degoo Setup ───────────────────────────────────────────"; \
		echo ""; \
		echo "  How will you log in?"; \
		echo "    browser  — Google Sign-In (OAuth, no password needed)"; \
		echo "    password — Degoo email + password"; \
		echo ""; \
		printf "Login method [browser]: "; \
		read -r dg_method; \
		dg_method=$${dg_method:-"browser"}; \
		echo ""; \
		if [ "$$dg_method" = "password" ]; then \
			printf "Install browser support for 'cligoo login --browser' (for future use)? [y/N]: "; \
			read -r dg_browser; \
			dg_browser=$${dg_browser:-"N"}; \
		else \
			printf "Install browser dependencies now for 'cligoo login --browser'? [Y/n]: "; \
			read -r dg_browser; \
			dg_browser=$${dg_browser:-"Y"}; \
		fi; \
		python3 -c "import sys,os,pathlib,tempfile; t,s,d=sys.argv[1:]; c=pathlib.Path(s).read_text().replace('__TOKEN_DIR__',t); dp=pathlib.Path(d); dp.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(dir=dp.parent,prefix='.cligoo-cfg-'); os.chmod(tmp,0o600); fh=os.fdopen(fd,'w'); fh.write(c); fh.close(); os.rename(tmp,d)" \
			"$(TOKEN_DIR)" "$(CONFIG_SAMPLE)" "$(CONFIG_DEST)"; \
		echo ""; \
		echo "✓ Config written to $(CONFIG_DEST) (mode 600)"; \
		echo "  Credentials are stored securely in the system keyring — not in this file."; \
		echo ""; \
		if echo "$$dg_browser" | grep -iqE "^(y|yes)$$"; then \
			$(MAKE) install-browser; \
		fi; \
		echo ""; \
		if [ "$$dg_method" = "browser" ]; then \
			echo "  Next step: cligoo login --browser"; \
		else \
			echo "  Next step: cligoo login"; \
		fi; \
	else \
		echo "✓ Config already exists at $(CONFIG_DEST) (left unchanged)"; \
	fi

reconfigure: ## Reconfigure cligoo from scratch (login method + Chrome profile)
	@echo ""
	@echo "  This will reset your cligoo configuration."
	@echo "  Tokens and credentials are kept — only preferences are changed."
	@echo ""
	@"$(INSTALL_PATH)" config

uninstall: ## Remove standalone install and its venv (config preserved)
	@rm -f "$(INSTALL_PATH)"
	@rm -rf "$(INSTALL_DIR)"
	@echo "✓ Removed $(INSTALL_PATH)"
	@echo "✓ Removed $(INSTALL_DIR)"
	@echo "  Config preserved at $(CONFIG_DEST)"
	@echo "  To remove config:  rm -rf $(CONFIG_DIR)"

reinstall: uninstall install ## Uninstall then reinstall from scratch

# ── Dev tools (use project .venv, do not touch the standalone install) ────────
lint: venv ## Lint Python source files
	@$(PIP) install -e ".[dev]" --quiet --quiet
	$(PY) -m ruff check src/cligoo tests scripts
	$(PY) -m ruff format --check src/cligoo tests scripts

fmt: venv ## Auto-format Python source files
	@$(PIP) install -e ".[dev]" --quiet --quiet
	$(PY) -m ruff format src/cligoo tests scripts

test: venv ## Run the unit test suite
	@$(PIP) install -e ".[dev]" --quiet --quiet
	$(PY) -m pytest tests/ -v

run: ## Show installed CLI help
	"$(INSTALL_PATH)" --help

clean: ## Remove dev venv and build artifacts (standalone install is unaffected)
	rm -rf $(VENV) .pytest_cache .ruff_cache build dist *.egg-info src/*.egg-info
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Cleaned build artifacts"
	@echo "  Standalone install at $(INSTALL_PATH) unaffected — use 'make uninstall' to remove it"
