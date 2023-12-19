import collections, io, json, logging, re as regex, requests, tarfile
from pathlib import Path
from enum import Enum

DOWNLOAD_URL: str = "https://discord.com/api/download"
DOWNLOAD_PARAMS: str = "platform=linux&format=tar.gz"
BUILD_INFO_PATH: str = "resources/build_info.json"
_URL_VERSION_REGEX_RAW: str = r"\/(\d+)\.(\d+)\.(\d+)\/"
URL_VERSION_REGEX: regex.Pattern = regex.compile(_URL_VERSION_REGEX_RAW)
_VERSION_REGEX_RAW: str = r"(\d+)\.(\d+)\.(\d+)"
VERSION_REGEX: regex.Pattern = regex.compile(_VERSION_REGEX_RAW)

class ParsingVersionError(Exception):
	def __init__(self):
		super().__init__("Failed to parse Discord version")

class BuildInfoNotFoundInTarError(Exception):
	def __init__(self):
		super().__init__("Failed to find Discord build_info.json in tar file")

class BuildInfoNotFoundError(Exception):
	def __init__(self):
		super().__init__("Failed to find Discord build_info.json")

class ReleaseChannelMismatchError(Exception):
	def __init__(self):
		super().__init__("Release channels do not match")

class InstalledVersionSameError(Exception):
	def __init__(self):
		super().__init__("Attempted to install Discord version which is the same as the one installed")

class InstalledVersionNewerError(Exception):
	def __init__(self):
		super().__init__("Attempted to install Discord version which is older than the one installed")

def get_download_location(channel: str = "") -> str:
	"""
	Return the download location of the Discord tar.gz file.

	The release channel can be specified. Default is stable, but ptb and canary are also valid options.
	"""
	url: str = ""
	if channel and channel != "stable":
		url = "{}/{}?{}".format(DOWNLOAD_URL, channel, DOWNLOAD_PARAMS)
	else:
		url = "{}?{}".format(DOWNLOAD_URL, DOWNLOAD_PARAMS)
	logging.debug("Using Discord API URL \"{}\"".format(url))
	response: requests.models.Response = requests.get(url, allow_redirects=False)
	return response.headers["Location"]

# Why doesn't Python have enums built-in? Argh...
class Ord(Enum):
	GREATER_THAN = 1
	EQUAL_TO = 2
	LESS_THAN = 3

def get_latest_discord_version(channel: str = "") -> tuple[int, int, int]:
	"""
	Return the current version of Discord.

	The release channel can be specified. Default is stable, but ptb and canary are also valid options.
	"""
	regex_search: regex.Match[str] | None = URL_VERSION_REGEX.search(get_download_location(channel = channel))
	if regex_search is None:
		err = ParsingVersionError()
		err.add_note("Zero matches for regex \"{}\"".format(_URL_VERSION_REGEX_RAW))
		err.add_note("This might be a bug!")
		raise err
	else:
		return (int(regex_search.group(1)), int(regex_search.group(2)), int(regex_search.group(3)))

def parse_version(version: str) -> tuple[int, int, int]:
	"""
	Return a (int, int, int) from a version string.
	"""
	regex_match: regex.Match[str] | None = VERSION_REGEX.match(version)
	if regex_match is None:
		err = ParsingVersionError()
		err.add_note("Zero matches for regex \"{}\" on string \"{}\"".format(_VERSION_REGEX_RAW, version))
		raise err
	else:
		return (int(regex_match.group(1)), int(regex_match.group(2)), int(regex_match.group(3)))

def compare_versions(v1: tuple[int, int, int], v2: tuple[int, int, int]) -> Ord:
	if v1[0] > v2[0]:
		return Ord.GREATER_THAN
	elif v1[0] < v2[0]:
		return Ord.LESS_THAN
		
	else:
		if v1[1] > v2[1]:
			return Ord.GREATER_THAN
		elif v1[1] < v2[1]:
			return Ord.LESS_THAN
		else:
			if v1[2] > v2[2]:
				return Ord.GREATER_THAN
			elif v1[2] < v2[2]:
				return Ord.LESS_THAN
			else:
				return Ord.EQUAL_TO

def format_version(version: collections.abc.Collection[int]) -> str:
	"""
	Return a formatted version string from the `version` numbers.
	"""
	return ".".join(str(number) for number in version)

def download_discord(channel: str = "") -> bytes:
	"""
	Download the Discord tar, and return it in bytes.

	The release channel can be specified. Default is stable, but ptb and canary are also valid options.
	"""
	download_location: str = get_download_location(channel = channel)
	logging.info("Downloading Discord from " + download_location)
	return requests.get(download_location).content

def get_installed_build_info(path: Path) -> dict:
	installed_build_info_path: Path = path.joinpath(BUILD_INFO_PATH)
	if not installed_build_info_path.exists():
		err = BuildInfoNotFoundError()
		err.add_note("Expected path: " + str(installed_build_info_path))
		raise err
	elif not installed_build_info_path.is_file():
		raise IsADirectoryError(installed_build_info_path)
	return json.loads(installed_build_info_path.read_bytes())

def install_discord(path: Path, tar: bytes, force: bool = False, strict_channel: bool = True):
	"""
	Install Discord from the given tar bytes to the given path.

	Set `force` to True to skip checking if the given build is newer than an installed version. If checking fails and `force` is False, does not install.

	`strict_channel` determines whether installing Discord from a different channel than the existing install will be forbidden. If checking fails and `strict_channel` is True, does not install.
	"""
	tar_io: io.BytesIO = io.BytesIO(tar)
	logging.info("Opening tar file")
	tar_object: tarfile.TarFile = tarfile.open(fileobj = tar_io)

	# Get the Discord folder inside the tar.
	root_names: list[str] = ["Discord/", "DiscordPTB/", "DiscordCanary/"]
	root_members: list[tarfile.TarInfo] = []
	build_info: tarfile.TarInfo | None = None
	for item in tar_object.getmembers():
		for root_name in root_names:
			if item.path == "{}{}".format(root_name, BUILD_INFO_PATH):
				build_info = item
			if item.path.startswith(root_name):
				item.name = item.name.removeprefix(root_name)
				root_members.append(item)

	# Check build_info.json from the tar
	build_info_json: dict = {}
	if build_info:
		file = tar_object.extractfile(build_info)
		assert file is not None
		build_info_json = json.loads(file.read())
		logging.info("Discord version in archive is {} of release channel {}".format(build_info_json["version"], build_info_json["releaseChannel"]))
	else:
		if strict_channel or not force:
			err = BuildInfoNotFoundInTarError()
			err.add_note("Expected path in tar: " + BUILD_INFO_PATH)
		else:
			logging.warning("Could not find Discord build_info.json in tar at path \"{}\"!".format(BUILD_INFO_PATH))

	# Check build_info.json from the existing install.
	try:
		installed_build_info_json: dict = get_installed_build_info(path)
	
		logging.info("Existing Discord version is {} of release channel {}".format(installed_build_info_json["version"], installed_build_info_json["releaseChannel"]))
	
		# Check the release channel and abort the install if they mismatch.
		if build_info_json["releaseChannel"] != installed_build_info_json["releaseChannel"]:
			if strict_channel:
				err = ReleaseChannelMismatchError()
				err.add_note("Tar is channel \"{}\" while installed is channel \"{}\"".format(build_info_json["releaseChannel"], installed_build_info_json["releaseChannel"]))
				raise err
			else:
				logging.warning("Requested version has a different Discord release channel than existing installation! (tar=\"{}\", existing=\"{}\")".format(build_info_json["releaseChannel"], installed_build_info_json["releaseChannel"]))
		else:
			# Check the version and abort the install if the existing version is newer, unless `force` is True.
			build_compare: Ord = compare_versions(parse_version(build_info_json["version"]), parse_version(installed_build_info_json["version"]))
			if build_compare == Ord.LESS_THAN:
				if not force:
					err = InstalledVersionNewerError()
					err.add_note("Installed is version {} and requested is version {}".format(installed_build_info_json["version"], build_info_json["version"]))
					raise err
				else:
					logging.warning("Installed version ({}) is newer than the version requested ({})!".format(installed_build_info_json["version"], build_info_json["version"]))
			elif build_compare == Ord.EQUAL_TO:
				if not force:
					err = InstalledVersionSameError()
					err.add_note("Installed is version {} and requested is version {}".format(installed_build_info_json["version"], build_info_json["version"]))
					raise err
				else:
					logging.warning("Installed version ({}) is the same version as the version requested ({})!".format(installed_build_info_json["version"], build_info_json["version"]))
	except BuildInfoNotFoundError as err:
		if strict_channel or not force:
			raise err
		else:
			logging.warning("Could not find build_info.json of existing installation at path \"{}\"!".format(path.joinpath(BUILD_INFO_PATH)))
			

	# If we got this far:
	#   the requested version should be NEWER than the installed version or from a different channel,
	#   `force` should be True if the requested version is equal to or older than the installed release from the same channel, and
	#   the requested version should be from the SAME channel if `strict_channel` is True.

	if path.exists():
		logging.info("Removing old Discord installation at \"{}\"".format(path))
		remove_directory(path)
	path.mkdir(parents=False)

	logging.info("Extracting tar file to \"{}\"".format(path))
	tar_object.extractall(path = path, members = root_members, filter = "data")
	tar_object.close()

def check_for_updates(path: Path) -> tuple[Ord, tuple[int, int, int], tuple[int, int, int]]:
	"""
	Check if there are updates available to the given Discord installation at path.
	"""
	# Check build_info.json from the existing install.
	installed_build_info_json: dict = get_installed_build_info(path)
	channel: str = "stable"
	if installed_build_info_json["releaseChannel"] == "canary" or installed_build_info_json["releaseChannel"] == "ptb":
		channel = installed_build_info_json["releaseChannel"]

	current: tuple[int, int, int] = parse_version(installed_build_info_json["version"])
	latest: tuple[int, int, int] = get_latest_discord_version(channel = channel)

	return (compare_versions(current, latest), current, latest)

def download_and_install_discord(path, channel: str = "", force: bool = False, strict_channel: bool = True):
	"""
	Download and install discord to the given path.

	The release channel can be specified. Default is stable, but ptb and canary are also valid options.

	Set `force` to True to skip checking if the given build is newer than an installed version. If checking fails and `force` is False, does not install.

	`strict_channel` determines whether installing Discord from a different channel than the existing install will be forbidden. If checking fails and `strict_channel` is True, does not install.
	"""
	install_discord(path, download_discord(channel = channel), force, strict_channel)

def remove_directory(path):
	"""
	Remove a directory and its contents. Silently skips anything that isn't a directory.

	This might be in pathlib, but I didn't see it.
	"""
	if path.is_dir():
		for item in path.iterdir():
			if item.is_dir():
				remove_directory(item)
			else:
				item.unlink()
		path.rmdir()