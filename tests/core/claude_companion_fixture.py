"""Loopback Anthropic transport for the real installed Claude companion CLI."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer


@contextmanager
def transport(frozen, response):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass

        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= 2 * 1024 * 1024 or len(requests) >= 8:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(size))
            if 'count_tokens' in self.path:
                wire = b'{"input_tokens":100}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(wire)))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(wire)
                return
            requests.append(body)
            turn = len(requests)
            if turn == 1:
                block = {'type': 'tool_use', 'id': 'read_1', 'name': 'mcp__continuity__read_evidence',
                         'input': {'event_id': frozen['events'][0]['event_id'], 'offset': 0, 'limit': 1024}}
            elif turn == 2:
                block = {'type': 'tool_use', 'id': 'patch_1', 'name': 'mcp__continuity__submit_patch', 'input': {'response': response}}
            else:
                block = {'type': 'text', 'text': 'Pass submitted.'}
            stop = 'end_turn' if block['type'] == 'text' else 'tool_use'
            message = {'id': 'msg_'+str(turn), 'type': 'message', 'role': 'assistant', 'model': body['model'],
                       'content': [block], 'stop_reason': stop, 'stop_sequence': None,
                       'usage': {'input_tokens': 10, 'output_tokens': 5}}
            if not body.get('stream'):
                wire = json.dumps(message).encode()
                content_type = 'application/json'
            else:
                initial = {**message, 'content': [], 'stop_reason': None}
                events = [('message_start', {'message': initial})]
                if block['type'] == 'tool_use':
                    events += [('content_block_start', {'index': 0, 'content_block': {**block, 'input': {}}}),
                               ('content_block_delta', {'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])}})]
                else:
                    events += [('content_block_start', {'index': 0, 'content_block': {'type': 'text', 'text': ''}}),
                               ('content_block_delta', {'index': 0, 'delta': {'type': 'text_delta', 'text': block['text']}})]
                events += [('content_block_stop', {'index': 0}),
                           ('message_delta', {'delta': {'stop_reason': stop, 'stop_sequence': None}, 'usage': {'output_tokens': 5}}),
                           ('message_stop', {})]
                wire = ''.join('event: '+kind+'\ndata: '+json.dumps({'type': kind, **data})+'\n\n' for kind, data in events).encode()
                content_type = 'text/event-stream'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:'+str(server.server_port), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
