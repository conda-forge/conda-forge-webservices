#!/usr/bin/env bash
set -x

STORAGE_LOCN=$(pwd)

# ----------

mkdir -p "$1" "$2" "$3"
build=$BUILD_DIR
cache=$CACHE_DIR
env_dir=$ENV_DIR

# -------

wget -q https://repo.continuum.io/miniconda/Miniconda3-4.6.14-Linux-x86_64.sh -O miniconda.sh
bash miniconda.sh -b -p $HOME/.conda
source $HOME/.conda/etc/profile.d/conda.sh
conda activate
conda config --add channels conda-forge
# need conda-smithy>=3.6.6 to get the bot automerge files
conda install --yes "conda-smithy>=3.6.6" conda-forge-pinning "conda=4.6" conda-build "python=3.7" tornado pygithub git statuspage "ruamel.yaml=0.16"
conda clean -tipsy --yes

conda info
conda config --show-sources
conda list --show-channel-urls

mkdir -p "${STORAGE_LOCN}/.conda-smithy"
ln -s "${STORAGE_LOCN}/.conda-smithy" "${HOME}/.conda-smithy"
cp "$env_dir/GH_TOKEN" "${HOME}/.conda-smithy/github.token"
cp "$env_dir/CIRCLE_TOKEN" "${HOME}/.conda-smithy/circle.token"

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
