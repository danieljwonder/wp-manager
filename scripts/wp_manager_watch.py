# WP Manager update alerts — launcher for a Hermes no-agent cron job.
#
# Installed into <HERMES_HOME>/scripts/ by the wp_updates_watch tool (Hermes runs cron scripts only
# from that directory). It checks the watched WordPress sites for updates and prints only what is
# new since the last run; Hermes delivers the output, and empty output sends nothing.
# It never installs updates. Remove it by removing the cron job and deleting this file.
import importlib
import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path('__WPM_PLUGIN_ROOT__')
CORE_MODULE = "wp_manager_update_alerts_core"


def _load_core():
    init = PLUGIN_ROOT / "core" / "__init__.py"
    if not init.is_file():
        return None
    spec = importlib.util.spec_from_file_location(CORE_MODULE, init,
                                                  submodule_search_locations=[str(init.parent)])
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[CORE_MODULE] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    if _load_core() is None:
        print(f"WP Manager update alerts: the wp-manager plugin was not found at {PLUGIN_ROOT}. "
              "Remove this cron job, or run the wp_updates_watch setup again after reinstalling.")
        return 1
    settings = importlib.import_module(f"{CORE_MODULE}.settings")
    watch = importlib.import_module(f"{CORE_MODULE}.watch")
    if not settings.plugin_enabled():
        msg = watch.disabled_notice()
        if msg:
            print(msg)
        return 0
    watch.clear_disabled_notice()
    settings.set_getter(settings.hermes_config_getter)
    out = watch.run()
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
