from __future__ import print_function

import os
import random
import unittest

import github

from conda_forge_webservices.linting import comment_on_pr


class Test_comment_on_pr(unittest.TestCase):
    def test_comment_same_as_before(self):
        PR_number = 18
        message_to_post = ("Testing that a message isn't re-posted if it \n"
                           "was the same as before. ```{}```"
                           "".format(random.randint(100000, 200000)))
        for _ in range(2):
            msg = comment_on_pr('conda-forge', 'conda-forge-webservices', PR_number,
                                message_to_post)
  
        gh = github.Github(os.environ['GH_TOKEN'])
        linting_repo = gh.get_user('conda-forge').get_repo('conda-forge-webservices')
        pr = linting_repo.get_issue(PR_number)
        comments = list(pr.get_comments())

        if len(comments) > 1:
            self.assertNotEqual(comments[-1].body, comments[-2].body)

        self.assertMultiLineEqual(comments[-1].body, message_to_post)

        
if __name__ == '__main__':
    unittest.main()
