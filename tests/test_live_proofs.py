"""Live-check evidence cannot treat transient/incomplete failures as guarantees."""
from pathlib import Path
import sys
import unittest
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
from check_live import precondition_rejected,size_rejected


class Evidence(unittest.TestCase):
    def test_transient_incomplete_unrelated_errors_never_pass(self):
        for status in (200,201,308,401,403,404,410,429,500,503):
            response=httpx.Response(status,json={'error':{'code':status,'message':'unrelated failure'}})
            self.assertFalse(precondition_rejected(response),status)
            self.assertFalse(size_rejected(response),status)
        self.assertFalse(size_rejected(httpx.Response(400,json={'error':{'message':'Malformed request','errors':[{'reason':'badRequest'}]}})))
        self.assertFalse(precondition_rejected(httpx.Response(412,json={'error':{'code':412}})))
    def test_specific_provider_failures_required(self):
        self.assertTrue(precondition_rejected(httpx.Response(412,json={'error':{'code':412,'errors':[{'reason':'conditionNotMet'}]}})))
        self.assertTrue(size_rejected(httpx.Response(400,json={'error':{'code':400,'message':'The content length does not match the specified size','errors':[{'reason':'invalid'}]}})))
        self.assertTrue(size_rejected(httpx.Response(413)))


if __name__=='__main__':unittest.main()
