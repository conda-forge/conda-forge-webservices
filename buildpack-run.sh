#!/usr/bin/env bash
set -x

STORAGE_LOCN=$(pwd)

# ----------

mkdir -p "$1" "$2" "$3"
build=$BUILD_DIR
cache=$CACHE_DIR
env_dir=$ENV_DIR

# -------

# Secret variables aren't exported in the build phase, but they are available
# from the environment directory.
export "GH_TOKEN=$(cat $env_dir/GH_TOKEN)"
export "CIRCLE_TOKEN=$(cat $env_dir/CIRCLE_TOKEN)"

# -------

wget -q https://repo.continuum.io/miniconda/Miniconda3-4.3.30-Linux-x86_64.sh -O miniconda.sh
bash miniconda.sh -b -p $HOME/.conda
$HOME/.conda/bin/conda update conda --yes
$HOME/.conda/bin/conda install -c conda-forge --yes conda-smithy conda=4.3 python=3.6 tornado pygithub git statuspage
$HOME/.conda/bin/conda clean --all --yes

mkdir -p "${STORAGE_LOCN}/.conda-smithy"
ln -s "${STORAGE_LOCN}/.conda-smithy" "${HOME}/.conda-smithy"
echo "${GH_TOKEN}" > ${HOME}/.conda-smithy/github.token
echo "${CIRCLE_TOKEN}" > ${HOME}/.conda-smithy/circle.token

git config --global user.name "conda-forge-admin"
git config --global user.email "pelson.pub+conda-forge@gmail.com"
cp $HOME/.gitconfig $build/.gitconfig

cp -rf $HOME/.conda $STORAGE_LOCN/.conda

mkdir -p $build/.profile.d
cat <<-'EOF' > $build/.profile.d/conda.sh
    # append to path variable
    export PATH=$HOME/.conda/bin:$PATH

EOF
