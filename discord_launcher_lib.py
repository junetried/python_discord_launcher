import dasbus, discord_update_lib, logging, os, signal, subprocess, time
from dasbus.connection import SessionMessageBus
from dasbus.loop import EventLoop
from dasbus.server.interface import dbus_interface
from dasbus.typing import Int, Str
from dataclasses import dataclass
from desktop_entry_lib import DesktopAction, DesktopEntry, TranslatableKey
from discord_update_lib import ParsingVersionError, BuildInfoNotFoundInTarError, BuildInfoNotFoundError, ReleaseChannelMismatchError, InstalledVersionSameError, InstalledVersionNewerError
from discord_update_lib import format_version, get_latest_discord_version, Ord, parse_version, remove_directory
from enum import Enum
from multiprocessing import Pipe, Process
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Type
ICON_NAME: str = "discord.png"
NAMESPACE: list[str] = ["xyz", "strangejune", "DiscordLauncher"]
OBJECT_PATH: str = "/" + "/".join(NAMESPACE)
SERVICE_NAME: str = ".".join(NAMESPACE)

@dbus_interface(".".join(NAMESPACE))
@dataclass
class DiscordLauncher(object):
	_config: dict
	_version: tuple[int, int, int]
	_pid: Int
	def Version(self) -> tuple[Int, Int, Int]:
		return self._version

	def ReleaseChannel(self) -> Str:
		return self._config["release_channel"]

	def PID(self) -> Int:
		return self._pid

	def Stop(self):
		os.kill(self._pid, signal.SIGTERM)

class NoUpdateAvailableError(Exception):
	def __init__(self):
		super().__init__("No update to Discord is available")

class DiscordRunningError(Exception):
	def __init__(self):
		super().__init__("Discord was already running")

class DiscordNotRunningError(Exception):
	def __init__(self):
		super().__init__("Discord was not already running")

def get_installed_build_info(config: dict) -> dict:
	"""
	Return the build info of the Discord installation.
	"""
	return discord_update_lib.get_installed_build_info(Path(config["discord_path"]))
	
def get_sample_desktop_entry_path(path: Path) -> Path:
	"""
	Return the Discord desktop entry path that comes with the Discord installation at path.
	"""
	build_info: dict = discord_update_lib.get_installed_build_info(path)
	if build_info["releaseChannel"] == "canary" or build_info["releaseChannel"] == "ptb":
		return path.joinpath("discord-{}.desktop".format(build_info["releaseChannel"]))
	else:
		return path.joinpath("discord.desktop")

def create_desktop_entry(config: dict, force: bool = False):
	"""
	Create the launcher desktop entry.

	If `force` is True, will create a desktop entry regardless of whether it is enabled in config.
	"""
	if config["desktop_entry"]["enabled"] or force:
		logging.info("Reading Discord installation desktop entry")
		desktop_file_path: Path = Path(config["desktop_entry"]["path"])
		discord_desktop_file_path: Path = get_sample_desktop_entry_path(Path(config["discord_path"]))
		discord_desktop_entry: DesktopEntry = DesktopEntry.from_file(discord_desktop_file_path)
	
		# Modify the desktop entry
		discord_desktop_entry.Icon = str(Path(config["discord_path"]).joinpath(ICON_NAME))
		discord_desktop_entry.Exec = "{} {} update-run".format(Path(config["launcher_path"]).with_name(".venv").joinpath("bin/python"), config["launcher_path"])
		workingdir: Path | None = Path(config["launcher_path"]).parent
		if workingdir:
			discord_desktop_entry.Path = str(workingdir)
		else:
			# I'm torn on if this should be considered fatal or not, but I guess if we got this far, go wild.
			logging.error("Couldn't find the configured launcher_path parent directory! This is very likely to cause problems. Make sure this is set to an ABSOLUTE path!")
		if config["desktop_entry"]["tryexec"]:
			discord_desktop_entry.TryExec = config["launcher_path"]

		if config["desktop_entry"]["setup_action"]:
			action = DesktopAction()
			action.Name = TranslatableKey()
			action.Name.default_text = "Setup Launcher"
			action.Exec = "{} {}".format(Path(config["launcher_path"]).with_name(".venv").joinpath("bin/python"), config["launcher_path"])
			discord_desktop_entry.Actions["setup"] = action

		logging.info("Writing new discord_launcher desktop entry")
		discord_desktop_entry.write_file(config["desktop_entry"]["path"])
	else:
		logging.info("Not creating desktop entry because it is disabled.")

def install_discord_from_tar(config: dict, tar: bytes, force_desktop_entry: bool = False):
	"""
	Install Discord from tar to a directory that is empty or doesn't exist yet.
	
	This does *no* version checking and *will* remove any existing installation.

	If `force_desktop_entry` is true, will create a desktop entry regardless of whether it is enabled in config.
	"""
	try:
		stop_discord()
	except DiscordNotRunningError:
		pass
	discord_update_lib.install_discord(Path(config["discord_path"]), tar, force = True, strict_channel = False)
	create_desktop_entry(config, force = force_desktop_entry)

def uninstall_discord_desktop_file(config: dict):
	"""
	Uninstall the Discord desktop entry file if it is installed.
	"""
	desktop_entry_path: Path = Path(config["desktop_entry"]["path"])
	if not desktop_entry_path.exists():
		raise FileNotFoundError("Desktop entry did not already exist at \"{}\"".format(desktop_entry_path))
	else:
		logging.info("Removing discord-launcher desktop file at \"\{}\"".format(config["desktop_entry"]["path"]))
		desktop_entry_path.unlink(missing_ok = False)

def uninstall_discord(config: dict):
	"""
	Uninstall Discord.

	Will raise `FileNotFoundError` if the Discord installation didn't exist at the configured path,
	`NotADirectoryError` if the configured Discord installation path existed but wasn't a directory,
	`FileNotFoundError` if the Discord desktop entry didn't exist at the configured path,
	or `IsADirectoryError` if the configured Discord desktop entry path existed but wasn't a file.

	In the cases where the path didn't exist, this function will attempt to complete the uninstall process before raising the error.
	"""
	try:
		stop_discord()
	except DiscordNotRunningError:
		pass
	discord_path: Path = Path(config["discord_path"])
	desktop_entry_path: Path = Path(config["desktop_entry"]["path"])

	# Attempt to complete the uninstall process before raising an error.
	error: Exception | None = None

	logging.info("Removing Discord installation at \"{}\"".format(config["discord_path"]))
	if not discord_path.exists():
		error = FileNotFoundError("Discord installation did not already exist at \"{}\"".format(discord_path))
		logging.error(error)
	elif not discord_path.is_dir():
		raise NotADirectoryError("Discord installation specified at \"{}\" is not a directory".format(discord_path))
	else:
		remove_directory(discord_path)

	logging.info("Removing discord-launcher desktop file at \"\{}\"".format(config["desktop_entry"]["path"]))
	if not desktop_entry_path.exists():
		e = FileNotFoundError("Desktop entry did not already exist at \"{}\"".format(desktop_entry_path))
		logging.error(e)
		if error is None:
			error = e
		else:
			error.add_note(str(e))
	elif not desktop_entry_path.is_file():
		raise IsADirectoryError("Desktop entry specified at \"{}\" is not a file".format(desktop_entry_path))
	else:
		desktop_entry_path.unlink(missing_ok = False)

	if error is not None:
		raise error

def check_for_updates(config: dict) -> tuple[Ord, tuple[int, int, int], tuple[int, int, int]]:
	"""
	Check if there are updates to Discord.
	"""
	build_info: dict = get_installed_build_info(config)
	if config["release_channel"] != build_info["releaseChannel"]:
		raise ReleaseChannelMismatchError((config["release_channel"], build_info["releaseChannel"]))

	return discord_update_lib.check_for_updates(Path(config["discord_path"]))

def update_discord(config: dict, strict_channel: bool = True) -> tuple[int, int, int]:
	"""
	Update Discord if there is an update available.

	`strict_channel` determines whether updating Discord from a different channel than the existing install will be forbidden.
	"""
	try:
		update_info: tuple[Ord, tuple[int, int, int], tuple[int, int, int]] | None = check_for_updates(config)
		match update_info[0]:
			case Ord.EQUAL_TO:
				err = NoUpdateAvailableError()
				err.add_note("Latest available version is {}, which is installed".format(format_version(update_info[2])))
				raise err
			case Ord.GREATER_THAN:
				err = ValueError("Installed Discord version is newer than latest available Discord version!")
				err.add_note("Installed version is {}, latest available is {}".format(format_version(update_info[1]), format_version(update_info[2])))
				raise err
			case Ord.LESS_THAN:
				logging.info("An update to Discord is available, installing now")
	except ReleaseChannelMismatchError as err:
		update_info: tuple[Ord, tuple[int, int, int], tuple[int, int, int]] | None = None
		if strict_channel:
			raise err
		else:
			logging.info("Configured release channel has been changed to {}, installing now".format(config["release_channel"]))
			

	build_info: dict = get_installed_build_info(config)
	
	logging.info("Installed Discord version is {} of release channel {}.".format(format_version(update_info[1]), build_info["releaseChannel"]))
	if update_info is not None:
		logging.info("Latest available Discord version is {} of release channel {}.".format(format_version(update_info[2]), build_info["releaseChannel"]))

	try:
		stop_discord()
	except DiscordNotRunningError:
		pass
	discord_update_lib.download_and_install_discord(Path(config["discord_path"]), channel = config["release_channel"], strict_channel = strict_channel)
	create_desktop_entry(config)
	return update_info[2]

def install_discord(config: dict):
	"""
	Install Discord.
	
	This does *no* version checking and *will* remove any existing installation.
	"""
	try:
		stop_discord()
	except DiscordNotRunningError:
		pass
	discord_update_lib.download_and_install_discord(Path(config["discord_path"]), channel = config["release_channel"], force = True, strict_channel = False)
	create_desktop_entry(config)

def get_discord_binary(config: dict) -> Path:
	"""
	Return the path that the Discord binary is expected to be in.

	This does *not* verify that the path actually exists, is executable, etc..
	"""
	bin_name: str = "Discord"
	if config["release_channel"] == "ptb":
		bin_name = "DiscordPTB"
	elif config["release_channel"] == "canary":
		bin_name = "DiscordCanary"
	return Path(config["discord_path"]).joinpath(bin_name)

def is_discord_running(bus = None, timeout = 5) -> bool:
	"""
	Check if an instance of Discord is runnning by trying to connect to it through DBus.

	`bus` is an optional existing DBus connection to use instead of creating a new one.

	`timeout` is the maximum time to wait before considering the result to be False. Default is 5ms.
	"""
	localbus = False
	if not bus:
		bus = SessionMessageBus()
		localbus = True
	proxy = bus.get_proxy(SERVICE_NAME, OBJECT_PATH)

	output = True
	try:
		proxy.Ping(timeout = timeout)
	except (TimeoutError, dasbus.error.DBusError):
		output = False

	if localbus:
		bus.disconnect()

	return output

def stop_discord(bus = None, timeout = 5, blocking = True):
	"""
	Asks the running Discord launcher to stop if an instance is running.

	`bus` is an optional existing DBus connection to use instead of creating a new one.

	Will raise `DiscordNotRunningError` if Discord was not already running.

	`timeout` is the maximum time in milliseconds to wait before considering Discord to not already be running. Default is 5ms.

	If `blocking` is True, block until Discord is no longer running.
	"""
	localbus: bool = False
	if not bus:
		bus = SessionMessageBus()
		localbus = True
	proxy = bus.get_proxy(SERVICE_NAME, OBJECT_PATH)

	try:
		pid: int = int(proxy.PID(timeout = timeout))
		proxy.Stop(timeout = timeout)
		logging.debug("Sent Stop to {}".format(SERVICE_NAME))
		if blocking:
			while True:
				try:
					os.kill(pid, 0)
					time.sleep(0.05)
				except:
					break
			logging.debug("Discord is no longer running")
	except dasbus.error.DBusError:
		err = DiscordNotRunningError()
		err.add_note("Error connecting to {}".format(SERVICE_NAME))
		raise err

	if localbus:
		bus.disconnect()

def run_discord(config: dict, launch_args = []):
	"""
	Run the Discord installation described in `config`.

	This function will not return until Discord exits.

	This will **fail** if Discord is not already installed.
	"""
	#discord_process: Process = Process(target = _run_discord, args = [config], kwargs = { "launch_args": launch_args }, daemon = False)
	if is_discord_running():
		raise DiscordRunningError()
	if not launch_args:
		launch_args = config["launch_args"]
	bin: Path = get_discord_binary(config)
	build_info = get_installed_build_info(config)
	version = parse_version(build_info["version"])

	logging.debug("Spawning DBus process and waiting for intial response")
	(pipe, c2) = Pipe(True)
	dbus_process: Process = Process(target = _run_dbus_service, args = (c2, config, version, build_info["releaseChannel"]))
	dbus_process.start()
	response: None | dasbus.error.DBusError = pipe.recv()
	if response:
		logging.critical("Failed to start DBus service!")
		raise response
	del response

	#bus = SessionMessageBus()
	#bus.register_service(SERVICE_NAME)

	logging.debug("Running Discord at '{}' using launch args {}".format(bin, launch_args))
	discord_process = subprocess.Popen([bin] + launch_args, cwd = config["working_directory"])

	logging.debug("Discord PID is {}".format(discord_process.pid))
	pipe.send(discord_process.pid)
	# The type checker complains about "redefining" `response` here,
	# but I'm annoyed and I'm not changing it
	response: None | dasbus.error.DBusError = pipe.recv()
	if response:
		logging.critical("Failed to set DBus object!")
		os.kill(discord_process.pid, signal.SIGTERM)
		raise response
	del response
	#bus.publish_object(OBJECT_PATH, DiscordLauncher(config, version, discord_process.pid))
	#dbus_process = Process(target = _run_dbus_service2, args = (bus, EventLoop()))
	#dbus_process.start()
	discord_process.wait()
	dbus_process.terminate()

def _run_dbus_service2(bus: SessionMessageBus, loop: EventLoop):
	"""
	Run the DBus service and listen for messages.
	"""
	loop.run()

def _run_dbus_service(pipe: Connection, config: dict, version: tuple[int, int, int], release_channel: str):
	"""
	Run the DBus service and listen for messages.

	The `pipe` is used to get the PID of Discord after this service starts.
	"""
	try:
		bus = SessionMessageBus()
		bus.register_service(SERVICE_NAME)
	except Exception as err:
		pipe.send(err)
		return
	loop = EventLoop()
	pipe.send(None)

	try:
		bus.publish_object(OBJECT_PATH, DiscordLauncher(config, version, pipe.recv()))
	except Exception as err:
		pipe.send(err)
		return
	pipe.send(None)
	loop.run()

def _run_discord(config: dict, launch_args = []):
	if not launch_args:
		launch_args = config["launch_args"]
	bin: Path = get_discord_binary(config)
	logging.debug("Running Discord at '{}' using launch args {}".format(bin, launch_args))
	subprocess.Popen([bin] + launch_args).wait()

def update_and_run_discord(config: dict, launch_args = [], strict_channel = True):
	"""
	Update Discord if there is an update available and then run it.

	This will **fail** if Discord is not already installed.

	`strict_channel` determines whether updating Discord from a different channel than the existing install will be forbidden.
	"""
	update_discord(config, strict_channel = strict_channel)
	run_discord(config, launch_args = launch_args)
