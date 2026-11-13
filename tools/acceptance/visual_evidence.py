"""Bind a human visual review to an unchanged, delivered MCP camera frame.

This checks provenance, not image semantics. The operator must independently read
the originals and retain the Agent's declaration; neither flag is inferred here.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def bind_review(protocol, participant, *, relay_sequence, sensor,
                agent_claimed_seen_other, seen_other_reviewed):
    protocol = Path(protocol).resolve(strict=True)
    events = [json.loads(line) for line in protocol.read_text().splitlines() if line.strip()]
    matches = [e for e in events if e.get('sequence') == relay_sequence]
    if len(matches) != 1:
        raise ValueError('Select exactly one original MCP response')
    event = matches[0]
    result = event.get('result', {})
    if event.get('error') or result.get('isError') or event.get('request', {}).get('operation') != 'observe':
        raise ValueError('Select a successful observe response')
    blocks = result.get('content', [])
    try:
        observation = json.loads(blocks[0]['text'])
        frame = observation['state']['image_frames'][sensor]
        valid = (frame['source_metadata_available'] is True
            and all(observation[key] == participant[key] for key in
                ('brain_instance_id','run_id','robot_instance_id','world_instance_id','world_epoch'))
            and all(frame[key] == participant[key] for key in
                ('robot_instance_id','world_instance_id','world_epoch','topology_revision'))
            and type(frame['source_sequence']) is int and frame['source_sequence'] >= 0
            and type(frame['captured_sim_time']) in (int,float) and math.isfinite(frame['captured_sim_time'])
            and type(agent_claimed_seen_other) is bool and type(seen_other_reviewed) is bool)
        if not valid:
            raise ValueError('Invalid acquisition')
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError('Incomplete or mismatched camera acquisition identity') from error
    images, current = [], None
    for block in blocks[1:]:
        if block.get('type') == 'text' and block.get('text', '').startswith('Sensor: '):
            current = block['text'].removeprefix('Sensor: ')
        elif block.get('type') == 'image' and current == sensor:
            images.append(block)
    if len(images) != 1 or images[0].get('mimeType') != 'image/png':
        raise ValueError('Select exactly one retained PNG for this sensor')
    image = Path(images[0]['path'])
    if image.is_symlink() or image.resolve(strict=True).parent != protocol.parent:
        raise ValueError('The original PNG must remain inside its relay directory')
    actual = hashlib.sha256(image.read_bytes()).hexdigest()
    if actual != frame['image_sha256']:
        raise ValueError('The retained PNG differs from the delivered image hash')
    return dict(robot_instance_id=participant['robot_instance_id'],
        world_instance_id=participant['world_instance_id'], world_epoch=participant['world_epoch'],
        topology_revision=participant['topology_revision'], sim_time=frame['captured_sim_time'],
        observation_sequence=frame['source_sequence'], image_sha256=actual,
        image_path=str(image), protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
        relay_sequence=relay_sequence, receipt_id=observation.get('receipt_id'), sensor=sensor,
        agent_claimed_seen_other=agent_claimed_seen_other, seen_other_reviewed=seen_other_reviewed)
