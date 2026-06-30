import json
import sys
import types

import pytest


class FakeContext:
    def IsCaptureLoaded(self):
        return True


def install_fake_renderdoc():
    fake_rd = types.SimpleNamespace()
    fake_rd.ResourceId = types.SimpleNamespace(Null=lambda: "NULL")
    fake_rd.ShaderStage = types.SimpleNamespace(Vertex="Vertex")
    sys.modules["renderdoc"] = fake_rd
    for name in [
        "renderdoc_extension.renderdoc_facade",
        "renderdoc_extension.services",
        "renderdoc_extension.services.mesh_service",
    ]:
        sys.modules.pop(name, None)


def import_mesh_service():
    install_fake_renderdoc()
    from renderdoc_extension.services.mesh_service import MeshService

    return MeshService


def make_service(data):
    MeshService = import_mesh_service()
    service = MeshService(FakeContext(), lambda callback: callback(None))
    service._extract = lambda controller, event_id: (data, None)
    return service


def attr(name, slot, values, semantic_name=""):
    return {
        "name": name,
        "semantic_name": semantic_name,
        "vertex_buffer_slot": slot,
        "components": len(values[0]) if values else 0,
        "values": values,
    }


def mesh_data(attributes):
    return {
        "event_id": 184,
        "num_indices": 3,
        "num_vertices": 3,
        "indices": [0, 1, 2],
        "attributes": attributes,
    }


def test_export_mesh_auto_slots_treat_two_component_input_as_uv0(tmp_path):
    output_path = tmp_path / "mesh.json"
    service = make_service(mesh_data([
        attr("_input0", 0, [[0, 0, 0], [1, 0, 0], [0, 1, 0]]),
        attr("_input2", 2, [[0, 0], [1, 0], [0, 1]]),
    ]))

    result = service.export_mesh_to_file(
        184,
        str(output_path),
        bake_world=False,
        pos_slot=-1,
        normal_slot=-1,
        tangent_slot=-1,
        uv0_slot=-1,
        uv1_slot=-1,
        extra_slot=-1,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["position"] == [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    assert payload["uv0"] == [[0, 0], [1, 0], [0, 1]]
    assert "tangent" not in payload
    assert result["has_uv0"] is True
    assert result["has_tangent"] is False
    assert result["slot_map"]["position"] == 0
    assert result["slot_map"]["uv0"] == 2


def test_export_mesh_auto_slots_preserve_common_unity_order(tmp_path):
    output_path = tmp_path / "mesh.json"
    service = make_service(mesh_data([
        attr("_input0", 0, [[0, 0, 0], [1, 0, 0], [0, 1, 0]]),
        attr("_input1", 1, [[0, 0, 1], [0, 0, 1], [0, 0, 1]]),
        attr("_input2", 2, [[1, 0, 0, 1], [1, 0, 0, 1], [1, 0, 0, 1]]),
        attr("_input3", 3, [[0, 0], [1, 0], [0, 1]]),
        attr("_input4", 4, [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]]),
    ]))

    result = service.export_mesh_to_file(
        184,
        str(output_path),
        bake_world=False,
        pos_slot=-1,
        normal_slot=-1,
        tangent_slot=-1,
        uv0_slot=-1,
        uv1_slot=-1,
        extra_slot=-1,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["normal"] == [[0.0, 0.0, 1.0]] * 3
    assert payload["tangent"] == [[1.0, 0.0, 0.0, 1]] * 3
    assert payload["uv0"] == [[0, 0], [1, 0], [0, 1]]
    assert payload["uv1"] == [[0.5, 0.5]] * 3
    assert result["slot_map"] == {
        "position": 0,
        "normal": 1,
        "tangent": 2,
        "uv0": 3,
        "uv1": 4,
        "extra": None,
    }


def test_export_mesh_skips_short_optional_attributes(tmp_path):
    output_path = tmp_path / "mesh.json"
    service = make_service(mesh_data([
        attr("_input0", 0, [[0, 0, 0], [1, 0, 0], [0, 1, 0]]),
        attr("_input2", 2, [[0, 0], [1, 0], [0, 1]]),
    ]))

    result = service.export_mesh_to_file(
        184,
        str(output_path),
        bake_world=False,
        pos_slot=0,
        normal_slot=999,
        tangent_slot=2,
        uv0_slot=999,
        uv1_slot=999,
        extra_slot=999,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "tangent" not in payload
    assert result["has_tangent"] is False


def test_request_handler_defaults_mesh_slots_to_auto_and_accepts_camel_case():
    from renderdoc_extension.request_handler import RequestHandler

    calls = []

    class Facade:
        def export_mesh_to_file(self, *args):
            calls.append(args)
            return {"ok": True}

    handler = RequestHandler(Facade())

    response = handler.handle({
        "id": 1,
        "method": "export_mesh_to_file",
        "params": {"event_id": 184, "output_path": "mesh.json"},
    })

    assert "error" not in response
    assert calls[-1][3:9] == (-1, -1, -1, -1, -1, -1)

    response = handler.handle({
        "id": 2,
        "method": "export_mesh_to_file",
        "params": {
            "event_id": 184,
            "output_path": "mesh.json",
            "posSlot": 10,
            "normalSlot": 11,
            "tangentSlot": 12,
            "uv0Slot": 13,
            "uv1Slot": 14,
            "extraSlot": 15,
        },
    })

    assert "error" not in response
    assert calls[-1][3:9] == (10, 11, 12, 13, 14, 15)


def test_facade_invoke_preserves_replay_callback_exception():
    install_fake_renderdoc()
    from renderdoc_extension.renderdoc_facade import RenderDocFacade

    class Replay:
        def BlockInvoke(self, callback):
            try:
                callback("controller")
            except Exception:
                pass

    class Context:
        def Replay(self):
            return Replay()

    facade = RenderDocFacade(Context())

    with pytest.raises(RuntimeError) as excinfo:
        facade._invoke(lambda controller: (_ for _ in ()).throw(ValueError("boom")))

    message = str(excinfo.value)
    assert "boom" in message
    assert "Traceback" in message
