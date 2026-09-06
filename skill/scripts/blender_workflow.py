"""Small, project-scoped Blender helpers for the photo-to-Blender workflow.

Inside Blender: run(project_dir, 'prepare'|'render'|'inspect', stage='all').
Background: blender source.blend --background --python blender_workflow.py --
    --project PROJECT --action prepare --overwrite-derived

No network calls and no model generation. Inspection is structural, not a claim
of visual similarity. All writes are confined to the selected project folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import bpy
from mathutils import Vector

OWNER = 'photo_to_blender_workflow_v1'
CAMERAS = {
    'WF Hero': (1.35, -1.8, 1.0),
    'WF Front': (0, -1, 0),
    'WF Side': (1, 0, 0),
    'WF Top': (0, 0, 1),
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _path(project, value, *, must_exist=False):
    """Resolve before checking containment, including existing symlinks/junctions."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Project paths must be nonempty strings')
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project / candidate
    candidate = candidate.resolve()
    if candidate == project or not candidate.is_relative_to(project):
        raise ValueError(f'Path must name a file inside the project: {value}')
    if must_exist and not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def _write_json(project, relative, data):
    target = _path(project, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                 allow_nan=False), encoding='utf-8')


def _config(project):
    config = json.loads(_path(project, 'project.json', must_exist=True)
                        .read_text(encoding='utf-8-sig'))
    if config.get('schema_version') != 1:
        raise ValueError('Expected project.json schema_version: 1')
    if not isinstance(config.get('subject_collection'), str):
        raise ValueError('subject_collection must identify the model collection')
    scale = config.get('unit_scale_length', 1.0)
    if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError('unit_scale_length must be finite and greater than zero')
    protected_paths = {_path(project, 'project.json')}
    for ref in config.get('reference_images', []):
        protected_paths.add(_path(project, ref['path'], must_exist=True))
    if config.get('source_blend'):
        protected_paths.add(_path(project, config['source_blend'], must_exist=True))
    _path(project, config.get('blend_file', 'model/replica.blend'))
    render_paths = set()
    for job in config.get('render_jobs', []):
        output = _path(project, job['file'])
        if output in protected_paths:
            raise ValueError('Render output must not overwrite a reference or preserved source')
        if output in render_paths:
            raise ValueError('Each render job must use a distinct output file')
        render_paths.add(output)
        if output.suffix.lower() != '.png':
            raise ValueError('render_jobs.file must end in .png')
        if job.get('kind') not in ('gray', 'beauty'):
            raise ValueError('render_jobs.kind must be gray or beauty')
        for dimension in ('width', 'height'):
            value = job.get(dimension)
            if not isinstance(value, int) or isinstance(value, bool) or not 16 <= value <= 16384:
                raise ValueError(f'{dimension} must be an integer from 16 to 16384')
    return config


def _scene(config, name=None):
    scene_name = name or config.get('scene') or bpy.context.scene.name
    scene = bpy.data.scenes.get(scene_name)
    if scene is None:
        raise ValueError(f'Scene does not exist: {scene_name}')
    return scene


def _subject(config, scene):
    collection = bpy.data.collections.get(config['subject_collection'])
    if collection is None:
        raise ValueError(f'Subject collection does not exist: {config["subject_collection"]}')
    scene_names = {obj.name for obj in scene.objects}
    return [obj for obj in collection.all_objects
            if obj.name in scene_names and obj.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}
            and obj.get('wf_owner') != OWNER]


def _bounds(objects, scene):
    """Measure evaluated vertices, not transformed local bounding-box corners.

    Transforming a local AABB overestimates tilted circular or irregular parts.
    Temporary evaluated meshes include modifiers and are always released.
    """
    low_values, high_values = [math.inf] * 3, [-math.inf] * 3
    finite_count = 0
    depsgraph = scene.view_layers[0].depsgraph
    depsgraph.update()
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        try:
            mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
            if mesh is None:
                continue
            for vertex in mesh.vertices:
                point = evaluated.matrix_world @ vertex.co
                if all(math.isfinite(value) for value in point):
                    finite_count += 1
                    for axis in range(3):
                        low_values[axis] = min(low_values[axis], point[axis])
                        high_values[axis] = max(high_values[axis], point[axis])
        finally:
            evaluated.to_mesh_clear()
    if not finite_count:
        raise ValueError('Subject has no finite geometry bounds in the selected scene')
    low, high = Vector(low_values), Vector(high_values)
    if max(high - low) <= 1e-9:
        raise ValueError('Subject bounds are degenerate')
    corners = [Vector((x, y, z)) for x in (low.x, high.x)
               for y in (low.y, high.y) for z in (low.z, high.z)]
    return low, high, corners


def _tag(block, role):
    block['wf_owner'] = OWNER
    block['wf_role'] = role
    return block


def _owned(blocks, role):
    return next((block for block in blocks
                 if block.get('wf_owner') == OWNER and block.get('wf_role') == role), None)


def _rig_collection(scene):
    role = 'rig:' + scene.name
    collection = _owned(bpy.data.collections, role)
    if collection is None:
        collection = _tag(bpy.data.collections.new('WF | Cameras and neutral lights'), role)
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    return collection


def _gray_material():
    material = _owned(bpy.data.materials, 'gray_material')
    if material is None:
        material = _tag(bpy.data.materials.new('WF | Neutral gray'), 'gray_material')
    material.use_nodes = True
    bsdf = next(node for node in material.node_tree.nodes
                if node.type == 'BSDF_PRINCIPLED')
    bsdf.inputs['Base Color'].default_value = (0.38, 0.38, 0.38, 1)
    bsdf.inputs['Metallic'].default_value = 0
    bsdf.inputs['Roughness'].default_value = 0.72
    if 'Coat Weight' in bsdf.inputs:
        bsdf.inputs['Coat Weight'].default_value = 0
    material.diffuse_color = (0.38, 0.38, 0.38, 1)
    return material


def _camera(scene, rig, name, direction, center, diameter, corners, size):
    role = 'camera:' + scene.name + ':' + name
    obj = _owned(scene.objects, role)
    if obj is None:
        data = _tag(bpy.data.cameras.new(name), role)
        obj = _tag(bpy.data.objects.new(name, data), role)
        rig.objects.link(obj)
    obj.data.type = 'ORTHO'
    obj.location = center + Vector(direction).normalized() * diameter * 3
    obj.rotation_euler = (center - obj.location).to_track_quat('-Z', 'Y').to_euler()
    obj.data.clip_start = max(diameter * 0.001, 0.000001)
    obj.data.clip_end = max(diameter * 20, 1)
    old_resolution = (scene.render.resolution_x, scene.render.resolution_y,
                      scene.render.pixel_aspect_x, scene.render.pixel_aspect_y)
    try:
        scene.render.resolution_x, scene.render.resolution_y = size
        scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
        obj.data.ortho_scale = 1
        frame = obj.data.view_frame(scene=scene)
        frame_x = max(p.x for p in frame) - min(p.x for p in frame)
        frame_y = max(p.y for p in frame) - min(p.y for p in frame)
        inverse = obj.rotation_euler.to_quaternion().inverted()
        local = [inverse @ (point - center) for point in corners]
        span_x = max(p.x for p in local) - min(p.x for p in local)
        span_y = max(p.y for p in local) - min(p.y for p in local)
        obj.data.ortho_scale = max(span_x / frame_x, span_y / frame_y) * 1.16
    finally:
        (scene.render.resolution_x, scene.render.resolution_y,
         scene.render.pixel_aspect_x, scene.render.pixel_aspect_y) = old_resolution
    return obj


def _neutral_lights(scene, rig, center, diameter):
    specs = [('Key', (-1.5, -2.0, 2.5), 450, 1.3),
             ('Fill', (2.0, -1.0, 1.1), 100, 1.5),
             ('Rim', (0.8, 1.8, 2.0), 250, 1.0)]
    for label, direction, power, size in specs:
        role = 'light:' + scene.name + ':' + label
        obj = _owned(scene.objects, role)
        if obj is None:
            data = _tag(bpy.data.lights.new('WF ' + label, 'AREA'), role)
            obj = _tag(bpy.data.objects.new('WF ' + label, data), role)
            rig.objects.link(obj)
        obj.location = center + Vector(direction) * diameter
        obj.rotation_euler = (center - obj.location).to_track_quat('-Z', 'Y').to_euler()
        obj.data.color = (1, 1, 1)
        obj.data.energy = power * diameter ** 2
        obj.data.shape = 'DISK'
        obj.data.size = diameter * size
    role = 'world:' + scene.name
    world = _owned(bpy.data.worlds, role)
    if world is None:
        world = _tag(bpy.data.worlds.new('WF | Neutral world'), role)
    world.use_nodes = True
    background = next(node for node in world.node_tree.nodes if node.type == 'BACKGROUND')
    background.inputs['Color'].default_value = (0.8, 0.8, 0.8, 1)
    background.inputs['Strength'].default_value = 0.25
    if scene.world and scene.world != world:
        scene['wf_previous_world'] = scene.world.name
    scene.world = world


def _pack_references(project, config):
    result = []
    for reference in config.get('reference_images', []):
        source = _path(project, reference['path'], must_exist=True)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        expected = reference.get('sha256')
        if expected and expected.lower() != digest:
            raise ValueError(f'Reference SHA-256 changed: {reference["path"]}')
        image = bpy.data.images.load(str(source), check_existing=True)
        image.use_fake_user = True
        image['wf_reference_sha256'] = digest
        image['wf_reference_original_name'] = reference.get('original_name', source.name)
        image.pack()
        result.append({'path': reference['path'], 'image': image.name, 'sha256': digest,
                       'packed': bool(image.packed_file), 'fake_user': image.use_fake_user})
    return result


def _check_derived(project, config, overwrite):
    target = _path(project, config.get('blend_file', 'model/replica.blend'))
    if target.suffix.lower() != '.blend':
        raise ValueError('blend_file must end in .blend')
    if config.get('source_blend') and target == _path(project, config['source_blend']):
        raise ValueError('blend_file must differ from the preserved source_blend')
    current = Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    marked = any(scene.get('wf_project_dir') == str(project) for scene in bpy.data.scenes)
    if current == target and not marked:
        raise ValueError('Refusing to overwrite the loaded source blend; choose a new blend_file')
    if target.exists() and not overwrite:
        raise FileExistsError('Derived blend already exists; explicitly pass overwrite_derived=True '
                              'or --overwrite-derived: ' + str(target))
    return target


def _save_derived(project, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    for scene in bpy.data.scenes:
        scene['wf_project_dir'] = str(project)
    bpy.ops.wm.save_as_mainfile(filepath=str(target))


def _resolve_camera(scene, name):
    if name in CAMERAS:
        obj = _owned(scene.objects, 'camera:' + scene.name + ':' + name)
        if obj:
            return obj
    obj = scene.objects.get(name) if name else scene.camera
    if obj is None or obj.type != 'CAMERA':
        raise ValueError(f'Camera does not exist in scene {scene.name}: {name}')
    return obj


def _file_output_nodes(scene):
    """File Output nodes are temporarily muted to confine render writes."""
    tree = getattr(scene, 'compositing_node_group', None) or getattr(scene, 'node_tree', None)
    pending, seen, nodes = [tree] if tree else [], set(), []
    while pending:
        current = pending.pop()
        if current.as_pointer() in seen:
            continue
        seen.add(current.as_pointer())
        for node in current.nodes:
            if node.type == 'OUTPUT_FILE':
                nodes.append(node)
            if node.type == 'GROUP' and node.node_tree:
                pending.append(node.node_tree)
    return nodes


def _render(project, config, stage):
    jobs = [job for job in config.get('render_jobs', [])
            if stage == 'all' or job['kind'] == stage]
    if not jobs:
        raise ValueError(f'No render_jobs match stage: {stage}')
    # Validate all selected destinations and cameras before a first render starts.
    resolved = [(job, _scene(config, job.get('scene')),
                 _path(project, job['file'])) for job in jobs]
    for job, scene, target in resolved:
        _resolve_camera(scene, job.get('camera'))
    report = {'schema_version': 1, 'created_at': _now(), 'stage': stage,
              'blender_version': bpy.app.version_string, 'jobs': []}
    gray = _gray_material() if any(job['kind'] == 'gray' for job in jobs) else None
    for job, scene, target in resolved:
        target.parent.mkdir(parents=True, exist_ok=True)
        render = scene.render
        image_settings = render.image_settings
        saved = {'camera': scene.camera, 'x': render.resolution_x,
                 'y': render.resolution_y, 'percentage': render.resolution_percentage,
                 'filepath': render.filepath, 'format': image_settings.file_format,
                 'color_mode': image_settings.color_mode, 'color_depth': image_settings.color_depth,
                 'aspect_x': render.pixel_aspect_x, 'aspect_y': render.pixel_aspect_y,
                 'extension': render.use_file_extension,
                 'use_compositing': render.use_compositing, 'use_sequencer': render.use_sequencer,
                 'file_output_nodes': [(node, node.mute) for node in _file_output_nodes(scene)],
                 'overrides': [(layer, layer.material_override) for layer in scene.view_layers]}
        entry = {'file': job['file'], 'scene': scene.name, 'camera': job.get('camera'),
                 'kind': job['kind'], 'width': job['width'], 'height': job['height'],
                 'engine': scene.render.engine, 'status': 'running'}
        started = time.monotonic()
        try:
            scene.camera = _resolve_camera(scene, job.get('camera'))
            render.resolution_x, render.resolution_y = job['width'], job['height']
            render.resolution_percentage = 100
            render.pixel_aspect_x = render.pixel_aspect_y = 1
            render.filepath = str(target)
            render.use_file_extension = True
            image_settings.file_format = 'PNG'
            image_settings.color_mode = 'RGBA' if render.film_transparent else 'RGB'
            image_settings.color_depth = '8'
            if job['kind'] == 'gray':
                render.use_compositing = False
                render.use_sequencer = False
                for layer in scene.view_layers:
                    layer.material_override = gray
            for node, muted in saved['file_output_nodes']:
                node.mute = True
            entry.update({'camera_actual': scene.camera.name,
                          'camera_type': scene.camera.data.type,
                          'camera_location': list(scene.camera.location),
                          'camera_rotation_euler': list(scene.camera.rotation_euler),
                          'camera_ortho_scale': scene.camera.data.ortho_scale,
                          'color_management': {'view_transform': scene.view_settings.view_transform,
                                               'look': scene.view_settings.look,
                                               'exposure': scene.view_settings.exposure,
                                               'gamma': scene.view_settings.gamma},
                          'cycles_samples': scene.cycles.samples if render.engine == 'CYCLES' else None})
            bpy.ops.render.render(write_still=True, scene=scene.name)
            # Read PNG IHDR rather than trusting requested dimensions or Render Result.
            import struct
            header = target.read_bytes()[:24]
            if header[:8] != b'\x89PNG\r\n\x1a\n':
                raise RuntimeError('Render did not produce a PNG file')
            actual = struct.unpack('>II', header[16:24])
            entry['actual_dimensions'] = list(actual)
            if actual != (job['width'], job['height']):
                raise RuntimeError(f'Unexpected rendered dimensions: {actual}')
            entry['status'] = 'complete'
            entry['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
        except BaseException as exc:
            entry['status'] = 'failed'
            entry['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            for layer, material in saved['overrides']:
                layer.material_override = material
            scene.camera = saved['camera']
            render.resolution_x, render.resolution_y = saved['x'], saved['y']
            render.resolution_percentage = saved['percentage']
            render.filepath = saved['filepath']
            image_settings.file_format = saved['format']
            image_settings.color_mode = saved['color_mode']
            image_settings.color_depth = saved['color_depth']
            render.pixel_aspect_x, render.pixel_aspect_y = saved['aspect_x'], saved['aspect_y']
            render.use_file_extension = saved['extension']
            render.use_compositing = saved['use_compositing']
            render.use_sequencer = saved['use_sequencer']
            for node, muted in saved['file_output_nodes']:
                node.mute = muted
            entry['elapsed_seconds'] = round(time.monotonic() - started, 3)
            report['jobs'].append(entry)
            _write_json(project, f'logs/render_{stage}.json', report)
    return report


def _inspect(project, config):
    dirty_at_entry = bpy.data.is_dirty
    loaded = Path(bpy.data.filepath) if bpy.data.filepath else None
    loaded_digest = hashlib.sha256(loaded.read_bytes()).hexdigest() if loaded and loaded.is_file() else None
    scene = _scene(config)
    subject = _subject(config, scene)
    issues = []
    bounds = None
    try:
        low, high, _ = _bounds(subject, scene)
        scale = scene.unit_settings.scale_length
        bounds = {'minimum_blender_units': list(low), 'maximum_blender_units': list(high),
                  'dimensions_blender_units': list(high - low),
                  'dimensions_mm': [v * scale * 1000 for v in high - low],
                  'scope': 'evaluated mesh vertices transformed to world space; subject_collection '
                           'in configured scene; includes evaluated mesh modifiers'}
    except ValueError as exc:
        issues.append(str(exc))
    nonfinite = []
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        count = sum(not all(math.isfinite(c) for c in vertex.co) for vertex in obj.data.vertices)
        if count:
            nonfinite.append({'object': obj.name, 'nonfinite_vertex_count': count})
    references = []
    for ref in config.get('reference_images', []):
        source = _path(project, ref['path'], must_exist=True)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        matches = [image for image in bpy.data.images
                   if image.get('wf_reference_sha256') == digest]
        references.append({'path': ref['path'], 'sha256': digest,
                           'source_matches_manifest': not ref.get('sha256') or digest == ref['sha256'].lower(),
                           'packed_images': [{'name': im.name, 'packed': bool(im.packed_file),
                                              'fake_user': im.use_fake_user} for im in matches],
                           'packed_and_persistent': any(im.packed_file and im.use_fake_user for im in matches)})
    user_lights = [obj.name for obj in scene.objects
                   if obj.type == 'LIGHT' and obj.get('wf_owner') != OWNER and not obj.hide_render]
    if user_lights:
        issues.append('Existing user lights remain active; neutral rig does not replace or hide them')
    if nonfinite:
        issues.append('Non-finite mesh vertices found')
    if any(not ref['packed_and_persistent'] for ref in references):
        issues.append('Some reference images are not packed with fake-user persistence')
    report = {'schema_version': 1, 'created_at': _now(), 'blender_version': bpy.app.version_string,
              'loaded_blend': bpy.data.filepath, 'loaded_blend_sha256': loaded_digest,
              'background_process': bpy.app.background, 'in_memory_dirty': dirty_at_entry,
              'subject_collection': config['subject_collection'],
              'subject_object_count': len(subject), 'scene_count': len(bpy.data.scenes),
              'scenes': [{'name': sc.name, 'object_count': len(sc.objects),
                          'camera': sc.camera.name if sc.camera else None,
                          'unit_system': sc.unit_settings.system,
                          'unit_scale_length': sc.unit_settings.scale_length} for sc in bpy.data.scenes],
              'configured_scene': scene.name, 'configured_unit_scale_length': config.get('unit_scale_length', 1),
              'actual_unit_scale_length': scene.unit_settings.scale_length,
              'unit_scale_matches_manifest': math.isclose(scene.unit_settings.scale_length,
                                                          config.get('unit_scale_length', 1), rel_tol=1e-6),
              'bounds': bounds, 'cameras': [{'name': obj.name, 'type': obj.data.type}
                                           for obj in bpy.data.objects if obj.type == 'CAMERA'],
              'nonfinite_meshes': nonfinite, 'references': references,
              'existing_active_user_lights': user_lights, 'issues': issues,
              'visual_similarity': 'not assessed; requires independent image comparison'}
    _write_json(project, 'logs/blender_inspection.json', report)
    return report


def run(project_dir, action, stage='all', overwrite_derived=False):
    """Run explicitly requested work; inspect never edits or saves the blend.

    prepare/render save only config.blend_file, never an unmarked loaded source.
    Existing derived files require explicit overwrite_derived=True on every run.
    render always restores material overrides and scene render settings, including
    when the renderer fails. It intentionally preserves existing beauty overrides.
    """
    project = Path(project_dir).expanduser().resolve()
    if not project.is_dir():
        raise FileNotFoundError(project)
    if action not in {'prepare', 'render', 'inspect'}:
        raise ValueError('action must be prepare, render or inspect')
    if stage not in {'gray', 'beauty', 'all'}:
        raise ValueError('stage must be gray, beauty or all')
    config = _config(project)
    if action == 'inspect':
        return _inspect(project, config)
    # Refuse accidental overwrites before changing anything in memory.
    target = _check_derived(project, config, overwrite_derived)
    if action == 'prepare':
        scene = _scene(config)
        subject = _subject(config, scene)
        low, high, corners = _bounds(subject, scene)
        center, diameter = (low + high) / 2, max(high - low)
        scene.unit_settings.system = 'METRIC'
        scene.unit_settings.scale_length = config.get('unit_scale_length', 1)
        rig = _rig_collection(scene)
        for name, direction in CAMERAS.items():
            matching_jobs = [job for job in config.get('render_jobs', [])
                             if job.get('camera') == name and job.get('scene', scene.name) == scene.name]
            sizes = [(job['width'], job['height']) for job in matching_jobs] or [(1200, 1200)]
            fit_scales = []
            for size in sizes:
                camera = _camera(scene, rig, name, direction, center, diameter, corners, size)
                fit_scales.append(camera.data.ortho_scale)
            camera.data.ortho_scale = max(fit_scales)
        _neutral_lights(scene, rig, center, diameter)
        _gray_material()
        references = _pack_references(project, config)
        scene.camera = _resolve_camera(scene, 'WF Hero')
        _save_derived(project, target)
        report = {'schema_version': 1, 'created_at': _now(), 'action': 'prepare',
                  'blend_file': str(target), 'references': references,
                  'note': 'Model transforms and user objects preserved; auto cameras are a starting point, '
                          'not an inferred match to the reference photograph.'}
        _write_json(project, 'logs/prepare.json', report)
        _inspect(project, config)
        return report
    # Existing camera/light setups may skip prepare; rendering still verifies and
    # packs references so the saved result is independently inspectable.
    references = _pack_references(project, config)
    report = _render(project, config, stage)
    report['references'] = references
    _write_json(project, f'logs/render_{stage}.json', report)
    _save_derived(project, target)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--action', required=True, choices=['prepare', 'render', 'inspect'])
    parser.add_argument('--stage', default='all', choices=['gray', 'beauty', 'all'])
    parser.add_argument('--overwrite-derived', action='store_true')
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    args = parser.parse_args(arguments)
    result = run(args.project, args.action, args.stage, args.overwrite_derived)
    print('WORKFLOW_RESULT ' + json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
