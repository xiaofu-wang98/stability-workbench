from uuid import uuid4

import pytest

from stability_hub.models import ImportContext, ItemDefinition, ProjectConfig
from stability_hub.permissions import Role, actor_for_role
from stability_hub.result_versions import ResultVersions
from stability_hub.storage import Store
from stability_hub.workflow import Workflow
from stability_hub.workflow_common import WorkflowError
from stability_hub.workflow_contracts import CandidateMapping, ConfirmItem, ConfirmRequest

TECH = actor_for_role('tech', '实验员', Role.TECHNICIAN)
NAMES = ('N端氨基酸序列 · -H缺失和-HG缺失', 'N端缺失含量 · RP-UPLC · -H缺失和-HG缺失')


@pytest.mark.parametrize('second_month,second_value', [('3M', '3.1'), ('0M', '3.5'), ('0M', '3.6')])
def test_cross_name_mapping_preserves_facts_and_blocks_same_identity_ambiguity(tmp_path, xlsx, context, second_month, second_value):
    config = ProjectConfig(project='PROJECT-P', items=(ItemDefinition(
        id='h_hg', label='N端-H与-HG缺失含量', method='RP-UPLC', unit='%'),))
    flow = Workflow(Store(tmp_path / 'store'))
    flow.configure(config)
    file = xlsx([[NAMES[0], '0M', '3.5', '%', ''],
                 [NAMES[1], second_month, second_value, '%', 'RP-UPLC'],
                 ['-HGEG缺失和其他修饰峰', '0M', '1.0', '%', 'RP-UPLC']],
                headers=['Test item', 'Time point', 'Result', 'Unit', 'Method'])
    before = flow.upload('PROJECT-P', file.name, file.read_bytes(), context, 'upload', actor=TECH)
    selected = before.candidates[:2]
    request = CandidateMapping(base_revision=before.base_revision, idempotency_key='merge',
                               reason='人工核对两种名称对应同一检测项',
                               candidate_ids=tuple(c.id for c in selected), item_id='h_hg')
    result = flow.map_items(before.preview_id, request, actor=TECH)
    assert flow.map_items(before.preview_id, request, actor=TECH) == result
    after = flow.preview(before.preview_id, actor=TECH)
    assert [c.raw_fields for c in after.candidates] == [c.raw_fields for c in before.candidates]
    assert [c.location for c in after.candidates] == [c.location for c in before.candidates]
    assert after.candidates[2].corrections == {}  # Different subresult must remain separate.
    assert after.candidates[2].status == 'new'
    assert after.candidates[2].record.key.item != 'h_hg'
    assert flow.configuration('PROJECT-P', actor=TECH)['config'] == config.model_dump(mode='json')
    assert flow.results('PROJECT-P', actor=TECH).total == 0
    assert all(c.record.key.item == 'h_hg' and c.record.key.method == 'RP-UPLC' for c in after.candidates[:2])
    if second_month == '0M':
        # Both equal and different values require explicit source disambiguation.
        assert all(c.issue_code == 'ambiguous_file_slot' for c in after.candidates[:2])
        assert all('confirm_initial' not in c.allowed_actions for c in after.candidates[:2])
        c = after.candidates[0]
        with pytest.raises(WorkflowError):
            ResultVersions(flow).confirm(before.preview_id, ConfirmRequest(
                base_revision=after.base_revision, idempotency_key=uuid4().hex,
                items=(ConfirmItem(candidate_id=c.id, candidate_revision=c.candidate_revision,
                                   detection_id=c.detection_id, identity_confirmed=True,
                                   intent='initial', reason='不得绕过歧义'),)), actor=TECH)
    else:
        assert all(c.status == 'new' for c in after.candidates[:2])


def test_mapping_does_not_overwrite_explicit_different_method_or_convert_unit(tmp_path, xlsx, context):
    flow = Workflow(Store(tmp_path / 'store'))
    flow.configure(ProjectConfig(project='PROJECT-P', items=(ItemDefinition(
        id='h_hg', label='N端-H与-HG缺失含量', method='RP-UPLC', unit='%'),)))
    file = xlsx([[NAMES[0], '0M', '3.5', '%', 'RP-HPLC'], [NAMES[1], '3M', '0.035', 'ratio', 'RP-UPLC']],
                headers=['Test item', 'Time point', 'Result', 'Unit', 'Method'])
    preview = flow.upload('PROJECT-P', file.name, file.read_bytes(), ImportContext(defaults=context.defaults), 'upload', actor=TECH)
    flow.map_items(preview.preview_id, CandidateMapping(
        base_revision=preview.base_revision, idempotency_key='merge', reason='仅统一检测项目名称',
        candidate_ids=tuple(c.id for c in preview.candidates), item_id='h_hg'), actor=TECH)
    after = flow.preview(preview.preview_id, actor=TECH)
    assert after.candidates[0].record.key.method == 'RP-HPLC'
    assert after.candidates[1].issue_code == 'unit_conflict'
    assert after.candidates[1].raw_fields['result'] == '0.035'
