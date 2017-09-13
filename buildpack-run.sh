#!/usr/bin/env bash
set -x

STORAGE_LOCN=$(pwd)

# ----------

mkdir -p "$1" "$2" "$3"
build=$(cd "$1/" && pwd)
cache=$(cd "$2/" && pwd)
env_dir=$(cd "$3/" && pwd)

# -------

wget -q https://repo.continuum.io/miniconda/Miniconda3-4.2.12-Linux-x86_64.sh -O miniconda.sh
bash miniconda.sh -b -p $HOME/.conda
$HOME/.conda/bin/conda update conda --yes
$HOME/.conda/bin/conda install -c conda-forge --yes conda-smithy python=3.5 tornado pygithub statuspage

mkdir -p $HOME/.conda-smithy
echo $GH_TOKEN > $HOME/.conda-smithy/github.token

cp -rf $HOME/.conda $STORAGE_LOCN/.conda

mkdir -p $build/.profile.d
cat <<-'EOF' > $build/.profile.d/conda.sh
    # append to path variable
    export PATH=$HOME/.conda/bin:$PATH

EOF
