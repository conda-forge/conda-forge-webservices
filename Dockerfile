FROM frolvlad/alpine-glibc:alpine-3.10

# much of image code ripped from
# https://github.com/Docker-Hub-frolvlad/docker-alpine-miniconda3

# license for docker image content
RUN echo "\n\
The MIT License (MIT)\n\
\n\
Copyright (c) 2016 Vlad\n\
\n\
Permission is hereby granted, free of charge, to any person obtaining a copy\n\
of this software and associated documentation files (the \"Software\"), to deal\n\
in the Software without restriction, including without limitation the rights\n\
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n\
copies of the Software, and to permit persons to whom the Software is\n\
furnished to do so, subject to the following conditions:\n\
\n\
The above copyright notice and this permission notice shall be included in all\n\
copies or substantial portions of the Software.\n\
\n\
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n\
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n\
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n\
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n\
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n\
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n\
SOFTWARE." > BASE_IMAGE_LICENSE

LABEL maintainer="conda-forge core (@conda-forge/core)"

ENV LANG en_US.UTF-8

ARG CONDA_VERSION="4.7.12.1"
ARG CONDA_MD5="81c773ff87af5cfac79ab862942ab6b3"
ARG CONDA_DIR="/opt/conda"

ENV PATH="$CONDA_DIR/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1

# make sure the install below is not cached by docker
ADD http://worldclockapi.com/api/json/utc/now /opt/docker/etc/timestamp

# Install conda
COPY conda-requirements.txt /
RUN echo "**** install dev packages ****" && \
    apk add --no-cache bash ca-certificates wget && \
    \
    echo "**** get Miniconda ****" && \
    mkdir -p "$CONDA_DIR" && \
    wget "http://repo.continuum.io/miniconda/Miniconda3-${CONDA_VERSION}-Linux-x86_64.sh" -O miniconda.sh && \
    echo "$CONDA_MD5  miniconda.sh" | md5sum -c && \
    \
    echo "**** install Miniconda ****" && \
    bash miniconda.sh -f -b -p "$CONDA_DIR" && \
    \
    echo "**** install base env ****" && \
    source /opt/conda/etc/profile.d/conda.sh && \
    conda activate base && \
    conda config --set show_channel_urls True  && \
    conda config --add channels conda-forge  && \
    conda config --show-sources  && \
    conda config --set always_yes yes && \
    conda update --all && \
    conda install --quiet --file conda-requirements.txt && \
    echo "**** cleanup ****" && \
    rm -rf /var/cache/apk/* && \
    rm -f miniconda.sh && \
    conda clean --all --force-pkgs-dirs --yes && \
    find "$CONDA_DIR" -follow -type f \( -iname '*.a' -o -iname '*.pyc' -o -iname '*.js.map' \) -delete && \
    \
    echo "**** finalize ****" && \
    mkdir -p "$CONDA_DIR/locks" && \
    chmod 777 "$CONDA_DIR/locks"

COPY entrypoint /opt/docker/bin/entrypoint
RUN mkdir -p conda_forge_webservices
COPY / conda_forge_webservices/
RUN cd conda_forge_webservices && \
    source /opt/conda/etc/profile.d/conda.sh && \
    conda activate base && \
    pip install -e .

CMD ["/opt/conda/bin/tini", \
     "--", \
     "/opt/docker/bin/entrypoint", \
     "python", \
     "-u", \
     "-m", \
     "conda_forge_webservices.webapp", \
    ]
