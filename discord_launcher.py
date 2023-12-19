import argparse, discord_launcher_lib, multiprocessing, logging, tomllib
from multiprocessing import freeze_support
from pathlib import Path

APP_NAME: str = "discord_launcher"
CONFIG_DIR: Path = Path.home().joinpath(".local/share").joinpath(APP_NAME)
CONFIG_FILE: Path = CONFIG_DIR.joinpath("config.toml")

def initialize_config(path: Path, launcher_path = Path(__file__).parent.resolve().joinpath("discord_launcher.py")):
	"""
	Initialize this launcher's config.

	`launcher_path` can be set to the path of the launcher that should appear in the desktop file.
	"""
	logging.info("Initializing config at {}".format(path))
	path.parent.mkdir(parents = True, exist_ok = True)
	path.write_text("discord_path = \"{}\"\nworking_directory = \"/usr/bin\"\nlaunch_args = []\nlauncher_path = \"{}\"\nrelease_channel = \"stable\"\n\n[desktop_entry]\nenabled = true\npath = \"{}\"\ntryexec = true\nsetup_action = false\n".format(CONFIG_DIR.joinpath("Discord"), launcher_path, Path.home().joinpath(".local/share/applications/{}.desktop".format(discord_launcher_lib.SERVICE_NAME))))

def verify_config_exists(config_path: Path):
	"""
	Make sure the config file exists.
	"""
	if not config_path.is_file():
		initialize_config(config_path)
	else:
		logging.debug("Config found at \"{}\"".format(config_path))

def read_config(config_path: Path) -> dict:
	"""
	Read the config at path and return a dict.
	"""
	verify_config_exists(config_path)
	return tomllib.loads(config_path.read_text())

def _get_latest_version(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	latest_version: tuple[int, int, int] = discord_launcher_lib.get_latest_discord_version(channel = config["release_channel"])
	print(discord_launcher_lib.format_version(latest_version))

def _get_installed_version(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	installed_build_info: dict = discord_launcher_lib.get_installed_build_info(config)
	print(installed_build_info["version"])

def _get_installed_channel(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	installed_build_info: dict = discord_launcher_lib.get_installed_build_info(config)
	print(installed_build_info["releaseChannel"])

def _check_updates(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	build_info: dict = discord_launcher_lib.get_installed_build_info(config)
	update_info: tuple[discord_launcher_lib.VersionOrd, tuple[int, int, int], tuple[int, int, int]] = discord_launcher_lib.check_for_updates(config)
	match update_info[0]:
		case discord_launcher_lib.VersionOrd.OLDER_THAN:
			print("There is an update available for Discord {}. Installed version is {}.{}.{} and latest available version is {}.{}.{}.".format(build_info["releaseChannel"], update_info[1][0], update_info[1][1], update_info[1][2], update_info[2][0], update_info[2][1], update_info[2][2]))
		case discord_launcher_lib.VersionOrd.EQUAL_TO:
			print("The currently installed version of Discord {} is {}.{}.{}, which is the latest available version.".format(build_info["releaseChannel"], update_info[1][0], update_info[1][1], update_info[1][2]))
		case discord_launcher_lib.VersionOrd.NEWER_THAN:
			print("The currently installed version of Discord {} is {}.{}.{} and latest available version is {}.{}.{}.".format(build_info["releaseChannel"], update_info[1][0], update_info[1][1], update_info[1][2], update_info[2][0], update_info[2][1], update_info[2][2]))
		case discord_launcher_lib.VersionOrd.CHANNEL_MISMATCH:
			print("The currently installed version of Discord is of the release channel {}, version {}.{}.{}, but the config specifies release channel {}.".format(build_info["releaseChannel"], update_info[1][0], update_info[1][1], update_info[1][2], config["release_channel"]))

def _stop(args: argparse.Namespace):
	try:
		discord_launcher_lib.stop_discord()
	except discord_launcher_lib.DiscordNotRunningError as err:
		logging.error(err)

def _update(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	discord_launcher_lib.update_discord(config, strict_channel=not args.allow_channel_swap)

def _install(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	discord_launcher_lib.install_discord(config)

def _install_desktop_entry(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	discord_launcher_lib.create_desktop_entry(config, force = True)

def _run(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	discord_launcher_lib.run_discord(config, launch_args = args.unhandled)

def _update_and_run(args: argparse.Namespace):
	config_file: Path = args.config
	config: dict = read_config(config_file)
	discord_launcher_lib.update_and_run_discord(config, launch_args = args.unhandled, strict_channel=not args.allow_channel_swap)

def _add_update_args(parser: argparse.ArgumentParser):
	parser.add_argument("--allow-channel-swap", "-C", action = "store_true", help = "Allow updating from a different channel if the config changes.")

def _add_run_args(parser: argparse.ArgumentParser):
	parser.add_argument("unhandled", metavar = "DISCORD ARGS", help = "Pass all unhandled/unrecognized arguments to Discord INSTEAD of the configured launch options.", nargs = "*")

def main():
	parser = argparse.ArgumentParser(
		prog = APP_NAME,
		description = "Launch and auto-update Discord.",
		epilog = "Any unhandled arguments will be passed to Discord to OVERRIDE the configured launch options. To invoke this, you can pass `--`, and any following arguments will always be unhandled.",
		formatter_class=argparse.ArgumentDefaultsHelpFormatter
	)
	parser.add_argument("--config", "-c", default = CONFIG_FILE, metavar = "CONFIG FILE", help = "Use a custom config location.", nargs = "?", type = Path)
	parser.add_argument("--log-level", "-v", default = "info", metavar = "LOGGING LEVEL", help = "Set the logging level. Possible options are `debug`, `info`, `warn`, and `error`.", nargs = "?")
	mode_subparser = parser.add_subparsers(prog = "mode", dest = "mode", help = "Operating mode.")
	get_latest_version_parser = mode_subparser.add_parser("latest-version", help = "Return the latest Discord version number.")
	get_latest_version_parser.set_defaults(func=_get_latest_version)
	get_installed_version_parser = mode_subparser.add_parser("installed-version", help = "Return the installed Discord version number.")
	get_installed_version_parser.set_defaults(func=_get_installed_version)
	get_installed_channel_parser = mode_subparser.add_parser("installed-channel", help = "Return the installed Discord channel.")
	get_installed_channel_parser.set_defaults(func=_get_installed_channel)
	check_update_parser = mode_subparser.add_parser("check-updates", help = "Check if the installed Discord has an update available.")
	check_update_parser.set_defaults(func=_check_updates)
	stop_parser = mode_subparser.add_parser("stop", help = "Attempt to stop a running Discord launcher instance.")
	stop_parser.set_defaults(func=_stop)
	update_parser = mode_subparser.add_parser("update", help = "Update Discord if there is an update available.")
	update_parser.set_defaults(func=_update)
	update_parser.add_argument("--force-update", "-f", action = "store_true", help = "Force install an update, even if an existing install can't be detected or isn't older than the latest version.")
	_add_update_args(update_parser)
	run_parser = mode_subparser.add_parser("run", help = "Run the Discord installation, WITHOUT updating. If there is an update available, Discord may refuse to work.", epilog = "Any unhandled arguments will be passed to Discord to OVERRIDE the configured launch options. To invoke this, you can pass `--`, and any following arguments will always be unhandled.")
	_add_run_args(run_parser)
	run_parser.set_defaults(func = _run)
	update_and_run_parser = mode_subparser.add_parser("update-run", help = "Update if there is an update available and run the Discord installation.", epilog = "Any unhandled arguments will be passed to Discord to OVERRIDE the configured launch options. To invoke this, you can pass `--`, and any following arguments will always be unhandled.")
	_add_update_args(update_and_run_parser)
	_add_run_args(update_and_run_parser)
	update_and_run_parser.set_defaults(func = _update_and_run)
	install_parser = mode_subparser.add_parser("install", help = "Install Discord. If there is an existing installation, it WILL be removed!")
	install_parser.set_defaults(func=_install)
	install_desktop_entry_parser = mode_subparser.add_parser("install-desktop-entry", help = "Install a Discord desktop entry. This will NOT work until you've installed Discord!")
	install_desktop_entry_parser.set_defaults(func=_install_desktop_entry)

	args = parser.parse_args()

	match args.log_level.lower():
		case "debug":
			logging.basicConfig(level = logging.DEBUG)
		case "warn" | "warning":
			logging.basicConfig(level = logging.WARNING)
		case "error":
			logging.basicConfig(level = logging.ERROR)
		case _:
			logging.basicConfig(level = logging.INFO)

	if args.mode:
		args.func(args)
	else:
		parser.parse_args(["--help"])

if __name__ == "__main__":
	# This should *only* run once.
	freeze_support()
	multiprocessing.set_start_method("spawn")
	main()
