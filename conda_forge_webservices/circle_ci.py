import requests
import os


def update_circle(user, project):
    if not project.endswith("-feedstock"):
        return

    try:
        # Create a token at https://circleci.com/account/api. Put it in circle.token
        with open(os.path.expanduser('~/.conda-smithy/circle.token'), 'r') as fh:
            circle_token = fh.read().strip()
    except IOError:
        print(
            'No circle token.  Create a token at https://circleci.com/account/api and\n'
            'put it in ~/.conda-smithy/circle.token')

    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json'}
    url_template = ('https://circleci.com/api/v1.1/project/github/{component}?'
                    'circle-token={token}')

    url = url_template.format(
        component='{}/{}/follow'.format(user, project).lower(), token=circle_token)
    requests.post(url, headers={})

    url = url_template.format(
        component='{}/{}/checkout-key'.format(user, project).lower(),
        token=circle_token)
    requests.post(url, headers=headers, json={'type': 'deploy-key'})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('org')
    parser.add_argument('repo')
    args = parser.parse_args()
    update_circle(args.org, args.repo)
