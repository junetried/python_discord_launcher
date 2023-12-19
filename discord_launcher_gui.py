import argparse, dasbus, discord_launcher_lib, gi, multiprocessing, logging, queue, tomlkit, traceback
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from multiprocessing import freeze_support, Pipe, Process
from multiprocessing.connection import Connection
from pathlib import Path

APP_NAME: str = "discord_launcher_gui"
APP_ID: str = ".".join(discord_launcher_lib.NAMESPACE)
CONFIG_DIR: Path = Path.home().joinpath(".local/share").joinpath("discord_launcher")
CONFIG_FILE: Path = CONFIG_DIR.joinpath("config.toml")
UI_FILE: Path = Path(__file__).parent.resolve().joinpath("launcher.glade")
MESSAGE_TIMEOUT: int = 1500
INFO_ICON: str = "dialog-information"
QUESTION_ICON: str = "dialog-question"
WARNING_ICON: str = "dialog-warning"
ERROR_ICON: str = "dialog-error"

def format_error(err: BaseException):
	string = err.__class__.__name__ + ": " + str(err)
	if len(err.__notes__) > 0:
		string += "; " + "; ".join(err.__notes__)
	return string

def assert_with(expression: bool, exception: Exception):
	if not expression:
		raise exception()

"""
Default configuration stuff.
"""

_DEFAULT_DISCORD_PATH: Path = CONFIG_DIR.joinpath("Discord")
_DEFAULT_WORKING_DIRECTORY: Path = Path("/usr/bin")
_DEFAULT_LAUNCHER_PATH: Path = Path(__file__).parent.resolve().joinpath("discord_launcher_gui.py")
_DEFAULT_DESKTOP_ENTRY_PATH: Path = Path.home().joinpath(".local/share/applications/{}.desktop".format(discord_launcher_lib.SERVICE_NAME))

class InvalidConfigValuesError(Exception):
	def __init__(self):
		super().__init__("One or more configuration values are invalid")

def default_config(launcher_path = _DEFAULT_LAUNCHER_PATH) -> str:
	"""
	Return a default config file.
	"""
	return "discord_path = \"{}\"\nworking_directory = \"{}\"\nlaunch_args = []\nlauncher_path = \"{}\"\nrelease_channel = \"stable\"\n\n[desktop_entry]\nenabled = true\npath = \"{}\"\ntryexec = true\nsetup_action = true\n".format(_DEFAULT_DISCORD_PATH, _DEFAULT_WORKING_DIRECTORY, launcher_path, _DEFAULT_DESKTOP_ENTRY_PATH)

def get_config(config_path: Path, launcher_path = Path(__file__).parent.resolve().joinpath("discord_launcher_gui.py")) -> (tomlkit.TOMLDocument, None | bool):
	"""
	Return the config at `config_path` or defaults if the file doesn't already exist.

	The defaults shouldn't be assumed to be usable; they are intended to be edited later.
	"""
	read_error = True
	try:
		text = config_path.read_text()
		read_error = False
		return (tomlkit.parse(text), None)
	except Exception:
		text = default_config(launcher_path = launcher_path)
		return (tomlkit.parse(text), read_error)

def verify_config_value_types(config: tomlkit.TOMLDocument):
	"""
	Assert that the values in the configuration are correct.
	"""
	assert_with(type(config["discord_path"]) is tomlkit.items.String, TypeError)
	assert_with(type(config["working_directory"]) is tomlkit.items.String, TypeError)
	assert_with(type(config["launch_args"]) is tomlkit.items.Array, TypeError)
	for arg in config["launch_args"]:
		assert_with(type(arg) is tomlkit.items.String, TypeError)
	assert_with(type(config["launcher_path"]) is tomlkit.items.String, TypeError)
	assert_with(type(config["release_channel"]) is tomlkit.items.String, TypeError)

	assert_with(type(config["desktop_entry"]["enabled"]) is bool, TypeError)
	if config["desktop_entry"]["enabled"]:
		assert_with(type(config["desktop_entry"]["path"]) is tomlkit.items.String, TypeError)
		assert_with(type(config["desktop_entry"]["tryexec"]) is bool, TypeError)
		assert_with(type(config["desktop_entry"]["setup_action"]) is bool, TypeError)

def verify_config_values(config: tomlkit.TOMLDocument):
	"""
	Assert that the values in the config are valid.
	"""
	assert_with(Path(config["discord_path"]).parent.exists(), InvalidConfigValuesError)
	assert_with(Path(config["working_directory"]).exists(), InvalidConfigValuesError)
	assert_with(Path(config["launcher_path"]).exists(), InvalidConfigValuesError)
	assert_with(config["release_channel"] in ["stable", "ptb", "canary"], InvalidConfigValuesError)

	if config["desktop_entry"]["enabled"]:
		assert_with(Path(config["desktop_entry"]["path"]).parent.exists(), InvalidConfigValuesError)

"""
The app stuff. This is where the fun begins!
"""

def _get_builder() -> Gtk.Builder:
		builder: Gtk.Builder = Gtk.Builder()
		builder.add_from_file(str(UI_FILE))
		return builder

def _check_latest_version(pipe: Connection):
	try:
		pipe.send(discord_launcher_lib.format_version(discord_launcher_lib.get_latest_discord_version()))
	except Exception as err:
		pipe.send("Error: " + str(err))

def _check_installed_version(pipe: Connection, config: dict):
	try:
		pipe.send(discord_launcher_lib.get_installed_build_info(config)["version"])
	except Exception as err:
		pipe.send("Error: " + str(err))

class SetupApp:
	def __init__(self, config_path: Path, editor: bool = False, update: bool = False, run: bool = False):
		"""
		Return value indicates whether the normal operation was interrupted.
		"""
		builder = _get_builder()

		builder.connect_signals(self)
		self.disabled = False
		# If this is true by the end of this init, we won't run an update or run command even if they were passed
		self._halt_actions = False
		self._flag_modified = False
		self._flag_unsaved = False
		self._latest_version_pipe: Connection | None = None
		self._latest_version_process: Process | None = None
		self.window = builder.get_object("setup_window"); assert self.window is not None
		self.tab_stack = builder.get_object("tab_stack"); assert self.tab_stack is not None
		self.config_path: Path = config_path
		self.config_path_label = builder.get_object("config_path_label"); assert self.config_path_label is not None

		# Important setup_tab widgets

		self.installed_version_label = builder.get_object("installed_version_label"); assert self.installed_version_label is not None
		self._installed_version_label_default = self.installed_version_label.get_text()
		self.latest_version_label = builder.get_object("latest_version_label"); assert self.latest_version_label is not None
		self._latest_version_label_default = self.latest_version_label.get_text()
		self.update_run_discord_button = builder.get_object("update_run_discord_button"); assert self.update_run_discord_button is not None
		self.install_discord_button = builder.get_object("install_discord_button"); assert self.install_discord_button is not None
		self.run_discord_button = builder.get_object("run_discord_button"); assert self.run_discord_button is not None
		self.update_discord_button = builder.get_object("update_discord_button"); assert self.update_discord_button is not None
		self.uninstall_discord_button = builder.get_object("uninstall_discord_button"); assert self.uninstall_discord_button is not None

		# Important friendly_editor_tab widgets

		self.discord_path_entry = builder.get_object("discord_path_entry"); assert self.discord_path_entry is not None
		self.working_directory_entry = builder.get_object("working_directory_entry"); assert self.working_directory_entry is not None
		self.add_argument_button = builder.get_object("add_argument_button"); assert self.add_argument_button is not None
		self.launch_arguments_box = builder.get_object("launch_arguments_box"); assert self.launch_arguments_box is not None
		self.launcher_path_entry = builder.get_object("launcher_path_entry"); assert self.launcher_path_entry is not None

		self.release_channel_combo = builder.get_object("release_channel_combo"); assert self.release_channel_combo is not None
		self.desktop_entry_enabled = builder.get_object("desktop_entry_enabled"); assert self.desktop_entry_enabled is not None
		self.desktop_entry_path_entry = builder.get_object("desktop_entry_path_entry"); assert self.desktop_entry_path_entry is not None
		self.tryexec_enabled = builder.get_object("tryexec_enabled"); assert self.tryexec_enabled is not None
		self.setup_action_enabled = builder.get_object("setup_action_enabled"); assert self.setup_action_enabled is not None

		self.friendly_editor_reload_button = builder.get_object("friendly_editor_reload_button"); assert self.friendly_editor_reload_button is not None
		self.friendly_editor_save_button = builder.get_object("friendly_editor_save_button"); assert self.friendly_editor_save_button is not None

		# Important editor_tab widgets

		self.editor_text_view = builder.get_object("editor_text_view"); assert self.editor_text_view is not None
		self.editor_reload_button = builder.get_object("editor_reload_button"); assert self.editor_reload_button is not None
		self.editor_save_button = builder.get_object("editor_save_button"); assert self.editor_save_button is not None

		# Dialog widgets

		self.error_dialog = builder.get_object("error_dialog"); assert self.error_dialog is not None
		self.error_label = builder.get_object("error_label"); assert self.error_label is not None
		self.error_text = builder.get_object("error_text"); assert self.error_text is not None
		self.error_copy_button = builder.get_object("error_copy_button"); assert self.error_copy_button is not None
		self._error_copy_button_default = self.error_copy_button.get_label()

		self.question_dialog = builder.get_object("question_dialog"); assert self.question_dialog is not None
		self.question_yes_button = builder.get_object("question_yes_button"); assert self.question_yes_button is not None
		self.question_no_button = builder.get_object("question_no_button"); assert self.question_no_button is not None
		self.question_label = builder.get_object("question_label"); assert self.question_label is not None

		self.last_page = self.tab_stack.get_visible_child_name()
		self.clipboard = Gtk.Clipboard.get_for_display(Gdk.Display.get_default(), Gdk.SELECTION_CLIPBOARD)

		self.reload_config(notify = False)
		self.update_config_controls()
		self.update_config_path_label()
		self.update_installed_version_label()
		self.update_latest_version_label()

		self.window.connect("destroy", Gtk.main_quit)

		if editor:
			# Force the friendly_editor_tab to be active
			self.tab_stack.set_visible_child_full("friendly_editor_tab", Gtk.StackTransitionType.NONE)

		if update and run:
			if not self._halt_actions:
				self.update_run_discord(queue = False)
			else:
				# This is maybe really dirty, but I don't care, fight me
				self.window.show_all()
				Gtk.main()
		elif update:
			if not self._halt_actions:
				self.update_discord()
			else:
				self.window.show_all()
				Gtk.main()
		elif run:
			if not self._halt_actions:
				self._run_discord()
			else:
				self.window.show_all()
				Gtk.main()

	def error(self, err: Exception, title: str = "Error", action: str | None = None, icon_name: str = ERROR_ICON) -> int:
		text = ""
		for line in traceback.format_exception(err):
			text += line
		self.error_text.get_buffer().set_text(text)
		m: str = "The following exception occurred"
		if action is not None:
			m += " while " + action
		m += ":"
		self.error_label.set_label(m)
		self.error_dialog.set_title(title)
		self.error_dialog.set_icon_name(icon_name)
		response = self.error_dialog.run()
		self.error_text.get_buffer().set_text("")
		self.error_dialog.hide()
		return response

	def _error_copy(self, *args):
		buffer = self.error_text.get_buffer()
		self.clipboard.set_text(str(buffer.get_text(*buffer.get_bounds(), False)), -1)
		self.error_copy_button.set_label("Copied!")
		self.error_copy_button.set_sensitive(False)
		GLib.timeout_add(MESSAGE_TIMEOUT, self._update_error_copy_button_text)

	def _update_error_copy_button_text(self):
		self.error_copy_button.set_label(self._error_copy_button_default)
		self.error_copy_button.set_sensitive(True)

	def message(self, message: str, title: str = "Message", button: str = "OK", icon_name: str = INFO_ICON) -> int:
		"""
		Show a prompt with one button and return the user input.
		"""
		self.question_dialog.set_title(title)
		self.question_dialog.set_icon_name(icon_name)
		self.question_label.set_label(message)
		self.question_yes_button.set_label(button)
		self.question_no_button.set_visible(False)
		response = self.question_dialog.run()
		self.reset_question_dialog()
		self.question_no_button.set_visible(True)
		return response

	def question(self, message: str, title: str = "Prompt", button0: str = "Yes", button0_timeout: int = 0, button1: str = "No", button1_timeout: int = 0, icon_name: str = QUESTION_ICON) -> int:
		"""
		Show a prompt with two buttons and return the user input.
		"""
		self.question_dialog.set_title(title)
		self.question_dialog.set_icon_name(icon_name)
		self.question_label.set_label(message)
		self.question_yes_button.set_label(button0)
		if button0_timeout > 0:
			self.question_yes_button.set_sensitive(False)
			GLib.timeout_add(button0_timeout, self.question_yes_button.set_sensitive, True)
		if button1_timeout > 0:
			self.question_no_button.set_sensitive(False)
			GLib.timeout_add(button1_timeout, self.question_no_button.set_sensitive, True)
		self.question_no_button.set_label(button1)
		response = self.question_dialog.run()
		self.reset_question_dialog()
		return response

	def confirm(self, action: str | None = None, note: str | None = None, accept_timeout: int = 0, title = "Warning", icon_name: str = WARNING_ICON) -> bool:
		"""
		Display an "are you sure" dialog warning.
		"""
		message: str = "Are you sure"
		if action is not None:
			message += " you want to {}?".format(action)
		else:
			message += "?"
		if note is not None:
			message += "\n\n" + note
		if self.question(message, title = title, button0_timeout = accept_timeout, icon_name = icon_name) == 0:
			return True
		return False

	def reset_question_dialog(self):
		"""
		Hide the question dialog and reset the widgets' state.
		"""
		self.question_dialog.hide()
		self.question_dialog.set_title("")
		self.question_label.set_label("")
		self.question_yes_button.set_label("")
		self.question_no_button.set_label("")

	def config_dict(self) -> dict:
		return self.config.value

	def update_config_path_label(self):
		self.config_path_label.set_label("Config path: " + str(self.config_path))

	def update_installed_version_label(self):
		self.installed_version_label.set_label(self._installed_version_label_default + "...")
		try:
			self.installed_version_label.set_label(self._installed_version_label_default + discord_launcher_lib.get_installed_build_info(self.config_dict())["version"])
		except Exception as err:
			self.installed_version_label.set_label(self._installed_version_label_default + format_error(err))

	def update_latest_version_label(self):
		self.latest_version_label.set_label(self._latest_version_label_default + "...")
		(self._latest_version_pipe, pipe) = Pipe(False)
		self._latest_version_process = Process(target = _check_latest_version, args = (pipe,))
		self._latest_version_process.start()
		GLib.timeout_add(200, self._check_latest_version_label)

	def _check_latest_version_label(self):
		if self._latest_version_pipe.poll(timeout = 0):
			self.latest_version_label.set_label(self._latest_version_label_default + self._latest_version_pipe.recv())
			self._latest_version_pipe = None
			self._latest_version_process = None
		else:
			# Come back later
			GLib.timeout_add(200, self._check_latest_version_label)

	def add_launch_argument(self, *args, arg: str | None = None):
		arg_box = Gtk.Box(orientation = Gtk.Orientation.HORIZONTAL, spacing = 6)
		self.launch_arguments_box.pack_start(arg_box, False, False, 0)
		arg_entry = Gtk.Entry()
		if arg is not None:
			arg_entry.set_text(arg)
		arg_entry.set_placeholder_text("--example-argument")
		arg_entry.set_input_hints(Gtk.InputHints.NO_SPELLCHECK)
		arg_entry.connect("changed", self.flag_modified)
		arg_box.pack_start(arg_entry, True, True, 0)
		remove_button = Gtk.Button.new_with_label("Remove")
		remove_button.connect("clicked", self.remove_launch_argument, arg_box)
		arg_box.pack_end(remove_button, False, False, 0)
		arg_box.show_all()
		self.flag_modified()

	def remove_launch_argument(self, _, box):
		self.launch_arguments_box.remove(box)
		self.flag_modified()

	def update_config_controls(self):
		"""
		This does *not* check if the values are correct!
		"""
		self.editor_text_view.get_buffer().set_text(self.config.as_string())
		self.discord_path_entry.set_text(self.config["discord_path"])
		self.working_directory_entry.set_text(self.config["working_directory"])
		for child in self.launch_arguments_box.get_children():
			child.destroy()
		for arg in self.config["launch_args"]:
			self.add_launch_argument(arg = arg)
		self.launcher_path_entry.set_text(self.config["launcher_path"])
		self.release_channel_combo.set_active_id(self.config["release_channel"])

		self.desktop_entry_enabled.set_active(self.config["desktop_entry"]["enabled"])
		try:
			self.desktop_entry_path_entry.set_text(self.config["desktop_entry"]["path"])
			self.tryexec_enabled.set_active(self.config["desktop_entry"]["tryexec"])
			self.setup_action_enabled.set_active(self.config["desktop_entry"]["setup_action"])
		except KeyError:
			# These config values are optional if the desktop entry is not enabled
			pass
		self.update_desktop_entry_controls_state()

	def update_desktop_entry_controls_state(self, *args):
		self.desktop_entry_path_entry.set_sensitive(self.desktop_entry_enabled.get_active())
		self.tryexec_enabled.set_sensitive(self.desktop_entry_enabled.get_active())
		self.setup_action_enabled.set_sensitive(self.desktop_entry_enabled.get_active())

	def config_from_friendly_editor(self) -> tomlkit.TOMLDocument:
		config = self.config_from_editor()
		config["discord_path"] = self.discord_path_entry.get_text()
		config["launch_args"] = []
		for child in self.launch_arguments_box.get_children():
			entry = child.get_children()[0]
			assert entry is not None
			config["launch_args"].append(entry.get_text())
		config["working_directory"] = self.working_directory_entry.get_text()
		config["launcher_path"] = self.launcher_path_entry.get_text()
		config["release_channel"] = self.release_channel_combo.get_active_id()

		config["desktop_entry"] = {}
		config["desktop_entry"]["enabled"] = self.desktop_entry_enabled.get_active()
		config["desktop_entry"]["path"] = self.desktop_entry_path_entry.get_text()
		config["desktop_entry"]["tryexec"] = self.tryexec_enabled.get_active()
		config["desktop_entry"]["setup_action"] = self.setup_action_enabled.get_active()
		return config

	def config_from_editor(self) -> tomlkit.TOMLDocument:
		buffer = self.editor_text_view.get_buffer()
		return tomlkit.parse(buffer.get_text(*buffer.get_bounds(), True))

	def flag_modified(self, *args):
		self._flag_modified = True
		self._flag_unsaved = True

	def verify_config(self, *args) -> bool:
		"""
		Verify if the configuration is correct, and disable some controls if it isn't.

		This function is ugly. I'm sorry.
		"""
		out = False
		keep_this_page = False
		if self._flag_modified:
			try:
				match self.last_page:
					case "setup_tab":
						# No settings to modify here, so ignore it
						pass
					case "friendly_editor_tab":
						config = self.config_from_friendly_editor()
						verify_config_value_types(config)
						verify_config_values(config)
						logging.debug("Config is OK")
						self.config = config
						self.editor_text_view.get_buffer().set_text(config.as_string())
						self.set_setup_state(True)
						self.set_editor_state(True)
						self.set_friendly_editor_state(True)
						self.update_installed_version_label()
						out = True
					case "editor_tab":
						config = self.config_from_editor()
						verify_config_value_types(config)
						verify_config_values(config)
						logging.debug("Config is OK")
						self.config = config
						self.update_config_controls()
						self.set_setup_state(True)
						self.set_editor_state(True)
						self.set_friendly_editor_state(True)
						self.update_installed_version_label()
						out = True
					case _:
						raise Exception("unexpected stack page!")
			# I was going to raise and catch ValueError,
			# but TOMLKit's exceptions derive ValueError,
			# and we do NOT want to match those here
			except InvalidConfigValuesError:
				# Config was parsed correctly but one or more values are invalid
				logging.debug("Config has invalid values")
				self.set_setup_state(False)
				self.config = config
			except Exception:
				# Config was not parsed correctly or one or moe value types are invalid
				logging.debug("Config is invalid")
				if self.tab_stack.get_visible_child_name() == self.last_page:
					match self.last_page:
						case "friendly_editor_tab":
							self.set_setup_state(False)
						case "editor_tab":
							self.set_editor_state(False)
							self.set_friendly_editor_state(False)
						case _:
							# Setup page does nothing, so won't throw an exception - don't match it here
							raise Exception("unexpected stack page!")
				else:
					match self.last_page:
						case "friendly_editor_tab":
							self.set_editor_state(False)
						case "editor_tab":
							self.set_setup_state(False)
							self.set_friendly_editor_state(False)
						case _:
							# Setup page does nothing, so won't throw an exception - don't match it here
							raise Exception("unexpected stack page!")
				# We don't want to verify the other edito page, because it's disabled
				keep_this_page = True
		else:
			logging.debug("Nothing was modified, not checking")

		if not keep_this_page:
			self.last_page = self.tab_stack.get_visible_child_name()
		self._flag_modified = False
		return out

	def set_editor_state(self, enabled: bool):
		self.editor_text_view.set_sensitive(enabled)
		self.editor_save_button.set_sensitive(enabled)

	def set_friendly_editor_state(self, enabled: bool):
		self.discord_path_entry.set_sensitive(enabled)
		self.working_directory_entry.set_sensitive(enabled)
		for child in self.launch_arguments_box.get_children():
			for c in child.get_children():
				c.set_sensitive(enabled)
		self.add_argument_button.set_sensitive(enabled)
		self.launcher_path_entry.set_sensitive(enabled)
		self.release_channel_combo.set_sensitive(enabled)
		self.desktop_entry_enabled.set_sensitive(enabled)
		self.desktop_entry_path_entry.set_sensitive(enabled)
		if enabled:
			self.update_desktop_entry_controls_state()
		else:
			self.desktop_entry_path_entry.set_sensitive(False)
			self.tryexec_enabled.set_sensitive(False)
			self.setup_action_enabled.set_sensitive(False)

	def set_setup_state(self, enabled: bool):
		self.update_run_discord_button.set_sensitive(enabled)
		self.run_discord_button.set_sensitive(enabled)
		self.update_discord_button.set_sensitive(enabled)
		self.install_discord_button.set_sensitive(enabled)
		self.uninstall_discord_button.set_sensitive(enabled)

	def update_run_discord(self, *args, nofail: bool = False, queue: bool = True):
		try:
			version = discord_launcher_lib.update_discord(self.config_dict(), strict_channel = False)
			self.message("Discord has been updated to version {}.".format(discord_launcher_lib.format_version(version)), title = "Alert")
			queue = True
			self.update_installed_version_label()
		except discord_launcher_lib.NoUpdateAvailableError:
			pass
		except Exception as err:
			self.error(err, action = "updating Discord")
			if not nofail:
				return

		if queue:
			self.run_discord()
		else:
			self._run_discord()

	def run_discord(self, *args):
		GLib.timeout_add(20, self._run_discord)
		self.window.hide()

	def _run_discord(self):
		try:
			discord_launcher_lib.run_discord(self.config_dict())
			self.window.show()
		except discord_launcher_lib.DiscordRunningError:
			self.message("Discord is already running. Please close it before running it again.", title = "Error", icon_name = ERROR_ICON)
			self.window.show()
		except Exception as err:
			self.error(err, action = "running Discord")
			self.window.show()


	def update_discord(self, *args):
		if self.confirm(action = "update Discord"):
			try:
				discord_launcher_lib.update_discord(self.config_dict(), strict_channel = False)
				self.message("Discord has been updated.", title = "Alert")
				self.update_installed_version_label()
			except discord_launcher_lib.NoUpdateAvailableError as warning:
				if len(warning.__notes__) == 1:
					note = warning.__notes__[0]
					if not note.endswith("."):
						note += "."
					self.message("Discord is already the latest version.\n" + note, title = "Alert")
				else:
					self.message("Discord is already the latest version.", title = "Alert")
			except Exception as err:
				self.error(err, action = "updating Discord")

	def install_discord(self, *args):
		note: str | None = None
		if Path(self.config["discord_path"]).exists():
			note = "This will REMOVE the Discord install directory!"
		if self.confirm(action = "install Discord", note = note):
			try:
				discord_launcher_lib.install_discord(self.config_dict())
				self.message("Discord has been installed.", title = "Alert")
				self.update_installed_version_label()
			except Exception as err:
				self.error(err, action = "installing Discord")

	def uninstall_discord(self, *args):
		if self.confirm(action = "uninstall Discord", accept_timeout = 1250):
			try:
				discord_launcher_lib.uninstall_discord(self.config_dict())
				self.message("Discord has been uninstalled.", title = "Alert")
				self.update_installed_version_label()
			except Exception as err:
				self.error(err, action = "uninstalling Discord")

	def reload_config(self, *args, notify = True):
		"""
		Reload the configuration from the current file.
		"""
		try:
			(self.config, read_error) = get_config(self.config_path)
			if read_error is not None:
				if read_error:
					self.message("Failed to read an existing configuration.\n(Does one already exist?)\nA new config has been generated for you.\nPlease save it before continuing.", title = "Alert", icon_name = WARNING_ICON)
					# wouldn't it be funny if the app asked you to save and then kept going anyway
					self._halt_actions = True
				else:
					self.message("Failed to parse the existing configuration.\nA new config has been generated for you.\nIf this is acceptable, please save it before continuing.\nIf not, close this app without saving it and edit the config at the path specified.", title = "Alert", icon_name = WARNING_ICON)
					# i agree, that wouldn't actually be very funny
					self._halt_actions = True
			self.update_config_controls()
			self.verify_config()
			self._flag_modified = False
			self._flag_unsaved = False
			if notify:
				self.config_path_label.set_label("Reloaded")
				GLib.timeout_add(MESSAGE_TIMEOUT, self.update_config_path_label)
		except Exception as err:
			self.error(err, action = "reloading the configuration")

	def save_config(self, *args):
		"""
		Save the configuration to the current file.
		"""
		try:
			self._flag_modified = True
			self.last_page = self.tab_stack.get_visible_child_name()
			if not self.verify_config():
				raise Exception("Configuration is invalid!")
			self._flag_modified = False
			self.config_path.parent.mkdir(parents = True, exist_ok = True)
			self.config_path.write_text(self.config.as_string())
			self._flag_unsaved = False
			self.config_path_label.set_label("Saved")
			GLib.timeout_add(MESSAGE_TIMEOUT, self.update_config_path_label)
		except Exception as err:
			self.error(err, action = "saving the configuration")

def main():
	parser = argparse.ArgumentParser(
		prog = APP_NAME,
		description = "Launch and auto-update Discord.",
		epilog = "Any unhandled arguments will be passed to Discord to OVERRIDE the configured launch options. To invoke this, you can pass `--`, and any following arguments will always be unhandled.",
		formatter_class = argparse.ArgumentDefaultsHelpFormatter
	)
	parser.add_argument("--config", "-c", default = CONFIG_FILE, metavar = "CONFIG FILE", help = "Use a custom config location.", nargs = "?", type = Path)
	parser.add_argument("--log-level", "-v", default = "info", metavar = "LOGGING LEVEL", help = "Set the logging level. Possible options are `debug`, `info`, `warn`, and `error`.", nargs = "?")
	mode_subparser = parser.add_subparsers(prog = "mode", dest = "mode", help = "Operating mode.")
	edit_config_parser = mode_subparser.add_parser("edit-config", help = "Edit the launcher config.")
	run_parser = mode_subparser.add_parser("run", help = "Run the Discord installation, WITHOUT updating. If there is an update available, Discord may refuse to work.")
	update_and_run_parser = mode_subparser.add_parser("update-run", help = "Update if there is an update available and run the Discord installation.")

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

	match args.mode:
		case "edit-config":
			app = SetupApp(args.config, editor = True)
			app.window.show_all()
			Gtk.main()
		case "run":
			app = SetupApp(args.config, run = True)
		case "update":
			app = SetupApp(args.config, update = True)
		case "update-run":
			app = SetupApp(args.config, update = True, run = True)
		case _:
			app = SetupApp(args.config)
			app.window.show_all()
			Gtk.main()

if __name__ == "__main__":
	# This should *only* run once.
	freeze_support()
	multiprocessing.set_start_method("spawn")
	main()