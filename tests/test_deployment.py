import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class DockerEntrypointTests(unittest.TestCase):
    def run_entrypoint(self, port=None):
        dockerfile = Path(__file__).resolve().parents[1] / 'Dockerfile'
        line = next(line for line in dockerfile.read_text().splitlines() if line.startswith('CMD '))
        command = json.loads(line[4:])
        with tempfile.TemporaryDirectory() as directory:
            # Capture command-line arguments instead of starting an actual web server.
            executable = Path(directory) / 'uvicorn'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            executable.chmod(0o755)
            env = {'PATH': directory + ':/usr/bin:/bin'}
            if port is not None:
                env['PORT'] = port
            output = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
            return output.stdout.splitlines()

    def test_render_port(self):
        arguments = self.run_entrypoint('10000')
        self.assertEqual(arguments[arguments.index('--port') + 1], '10000')
        self.assertEqual(arguments[arguments.index('--host') + 1], '0.0.0.0')

    def test_local_default(self):
        arguments = self.run_entrypoint()
        self.assertEqual(arguments[arguments.index('--port') + 1], '8000')

    def test_custom_port(self):
        arguments = self.run_entrypoint('8080')
        self.assertEqual(arguments[arguments.index('--port') + 1], '8080')
