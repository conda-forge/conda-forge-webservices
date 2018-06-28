#!/usr/bin/env bash
set -x

STORAGE_LOCN=$(pwd)

# ----------

mkdir -p "$1" "$2" "$3"
build=$BUILD_DIR
cache=$CACHE_DIR
env_dir=$ENV_DIR

# -------

wget -q https://repo.continuum.io/miniconda/Miniconda3-4.4.10-Linux-x86_64.sh -O miniconda.sh
bash miniconda.sh -b -p $HOME/.conda
source $HOME/.conda/etc/profile.d/conda.sh
conda activate
conda update conda --yes
conda install -c conda-forge --yes conda-smithy conda-forge-pinning conda=4.5 python=3.6 tornado pygithub git statuspage
conda clean --all --yes

mkdir -p "${STORAGE_LOCN}/.conda-smithy"
ln -s "${STORAGE_LOCN}/.conda-smithy" "${HOME}/.conda-smithy"
echo "${GH_TOKEN}" > ${HOME}/.conda-smithy/github.token
echo "${CIRCLE_TOKEN}" > ${HOME}/.conda-smithy/circle.token

git config --global user.name "conda-forge-admin"
git config --global user.email "pelson.pub+conda-forge@gmail.com"
mv "$HOME/.gitconfig" "$STORAGE_LOCN/.gitconfig"
ln -s "$STORAGE_LOCN/.gitconfig" "$HOME/.gitconfig"

cp -rf $HOME/.conda $STORAGE_LOCN/.conda

mkdir -p $build/.profile.d
cat <<-'EOF' > $build/.profile.d/conda.sh
    source $HOME/.conda/etc/profile.d/conda.sh
    conda activate

EOF

# -------

# Secret variables aren't exported in the build phase, but they are available
# from the environment directory.
export "GH_TOKEN=$(cat $env_dir/GH_TOKEN)"
export "CIRCLE_TOKEN=$(cat $env_dir/CIRCLE_TOKEN)"
