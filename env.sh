# Source this before running anything: `source env.sh`
# Puts the transit-lab conda env on PATH and points JAVA_HOME at the JDK *inside* the
# env (conda-forge openjdk 21). Upstream's env.sh uses Homebrew's keg-only openjdk
# instead; that is 26 here, and R5 is built and tested against the 21 LTS. JPype finds
# the JVM through JAVA_HOME, so it must be set, not just `java` on PATH.
export LAB_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]:-$0}" )" && pwd )"
export CONDA_ENV="/opt/homebrew/Caskroom/miniforge/base/envs/transit-lab"
export JAVA_HOME="$CONDA_ENV/lib/jvm"
export PATH="$CONDA_ENV/bin:$JAVA_HOME/bin:$PATH"
