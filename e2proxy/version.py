"""Version and build identification.

VERSION is the official release (bumped per PR, major/minor). The build id is
injected at image build time (``E2PROXY_BUILD_BRANCH`` / ``E2PROXY_BUILD_SEQ``,
see Dockerfile) so every rollout is uniquely identifiable without code edits.
"""
import os
import time

VERSION = "4.9"
BUILD_BRANCH = os.environ.get("E2PROXY_BUILD_BRANCH", "local")
BUILD_SEQ = os.environ.get("E2PROXY_BUILD_SEQ", "dev")[:7]  # CI passes the full git sha
INTERNAL_VERSION = f"{VERSION}+{BUILD_BRANCH.split('/')[-1]}.{BUILD_SEQ}"
START_TIME = time.time()  # process start, reported as uptime
