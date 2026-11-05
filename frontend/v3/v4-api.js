// V4 API Adapter & Contracts Client
// Communicates with backend endpoints or mock data conforming to contracts/v1/schema.json & HTTP revision 2

(function(global) {
  'use strict';

  // Base error structure conforming to ApiError
  class ApiClientError extends Error {
    constructor(code, message, details = {}) {
      super(message);
      this.name = 'ApiClientError';
      this.code = code;
      this.details = details;
    }
  }

  // Built-in mock actor (ONLY used when mode === 'mock')
  const MOCK_ACTOR = {
    id: 'example-local-user',
    display_name: '本地试点操作者',
    capabilities: ['view', 'preview', 'confirm_fact', 'review_duplicate', 'adopt_result', 'correct_result', 'export']
  };

  const MOCK_PREVIEW = {
    contract_version: '1',
    contract_revision: '2',
    preview_id: 'upload_mock_5d906e5909b6a1d5ca47824e',
    project: 'IP166E',
    filename: 'synthetic-example.xlsx',
    source_hash: '582e6748049e000e129d26fa0b1a977da4b5def9b25b12af95110d47efe347aa',
    upload_sequence: 1,
    uploaded_by: 'example-local-user',
    uploaded_at: '2026-10-11 12:00:00',
    base_revision: 1,
    state: 'preview_ready',
    candidates: [
      {
        id: 'cand-001',
        candidate_revision: 1,
        lifecycle: 'draft',
        detection_id: 'det-mock-001-retest',
        source_number: 'CoA-2025-0817',
        existing_detection_id: 'det-mock-orig-001',
        adopted_detection_id: 'det-mock-orig-001',
        test_date: '2026-10-10',
        corrections: {},
        allowed_actions: ['edit', 'withdraw', 'submit_review'],
        location: {
          format: 'xlsx',
          section: 'Results',
          row: 2,
          column: 3
        },
        raw_fields: {
          test_item: 'SEC-HPLC 单体纯度',
          time_point: '12M',
          result: '98.8%',
          unit: '%',
          source_number: 'CoA-2025-0817'
        },
        status: 'different_value',
        record: {
          key: {
            project: 'IP166E',
            sample_type: 'DP',
            batch: 'E202506002A1',
            presentation: '1mg (0.5ml)',
            study: 'long_term',
            study_id: 'STB(C)-QC-IP166E-P-2-005',
            condition: '5C',
            month: 12,
            item: 'SEC-HPLC 单体纯度',
            method: 'SEC-HPLC',
            method_version: 'not_reported'
          },
          result: {
            status: 'numeric',
            raw_text: '98.8%',
            value: '98.8',
            comparator: 'eq',
            unit: '%'
          }
        },
        existing_record: {
          key: {
            project: 'IP166E',
            sample_type: 'DP',
            batch: 'E202506002A1',
            presentation: '1mg (0.5ml)',
            study: 'long_term',
            study_id: 'STB(C)-QC-IP166E-P-2-005',
            condition: '5C',
            month: 12,
            item: 'SEC-HPLC 单体纯度',
            method: 'SEC-HPLC',
            method_version: 'Rev.02'
          },
          result: {
            status: 'numeric',
            raw_text: '98.1%',
            value: '98.1',
            comparator: 'eq',
            unit: '%'
          }
        },
        issue_code: null,
        detail: '检测值与当前有效记录不同 (98.8% vs 98.1%)；请核对是否为复测事实或更正。',
        comment: '复测记录待核对'
      },
      {
        id: 'cand-002',
        candidate_revision: 1,
        lifecycle: 'draft',
        detection_id: 'det-mock-002-same',
        source_number: 'CoA-2025-0817',
        existing_detection_id: 'det-mock-orig-002',
        adopted_detection_id: 'det-mock-orig-002',
        test_date: '2026-10-10',
        corrections: {},
        allowed_actions: ['edit', 'withdraw', 'submit_review'],
        location: {
          format: 'xlsx',
          section: 'Results',
          row: 3,
          column: 3
        },
        raw_fields: {
          test_item: 'icIEF 电荷异质性主峰',
          time_point: '12M',
          result: '53.0%',
          unit: '%',
          source_number: 'CoA-2025-0817'
        },
        status: 'same_value',
        record: {
          key: {
            project: 'IP166E',
            sample_type: 'DP',
            batch: 'E202506002A1',
            presentation: '1mg (0.5ml)',
            study: 'long_term',
            study_id: 'STB(C)-QC-IP166E-P-2-005',
            condition: '5C',
            month: 12,
            item: 'icIEF 电荷异质性主峰',
            method: 'icIEF',
            method_version: 'not_reported'
          },
          result: {
            status: 'numeric',
            raw_text: '53.0%',
            value: '53.0',
            comparator: 'eq',
            unit: '%'
          }
        },
        existing_record: {
          key: {
            project: 'IP166E',
            sample_type: 'DP',
            batch: 'E202506002A1',
            presentation: '1mg (0.5ml)',
            study: 'long_term',
            study_id: 'STB(C)-QC-IP166E-P-2-005',
            condition: '5C',
            month: 12,
            item: 'icIEF 电荷异质性主峰',
            method: 'icIEF',
            method_version: 'Rev.01'
          },
          result: {
            status: 'numeric',
            raw_text: '53.0%',
            value: '53.0',
            comparator: 'eq',
            unit: '%'
          }
        },
        issue_code: null,
        detail: '与已有有效记录数值相同。',
        comment: ''
      },
      {
        id: 'cand-003',
        candidate_revision: 1,
        lifecycle: 'draft',
        detection_id: 'det-mock-003-new',
        source_number: 'CoA-2025-0817',
        existing_detection_id: null,
        adopted_detection_id: null,
        test_date: '2026-10-10',
        corrections: {},
        allowed_actions: ['edit', 'withdraw', 'confirm_initial'],
        location: {
          format: 'xlsx',
          section: 'Results',
          row: 4,
          column: 3
        },
        raw_fields: {
          test_item: '生物学活性 (cAMP 检测)',
          time_point: '18M',
          result: '96%',
          unit: '%',
          source_number: 'CoA-2025-0817'
        },
        status: 'new',
        record: {
          key: {
            project: 'IP166E',
            sample_type: 'DP',
            batch: 'E202506002A1',
            presentation: '1mg (0.5ml)',
            study: 'long_term',
            study_id: 'STB(C)-QC-IP166E-P-2-005',
            condition: '5C',
            month: 18,
            item: '生物学活性 (cAMP 检测)',
            method: '细胞报告基因',
            method_version: 'not_reported'
          },
          result: {
            status: 'numeric',
            raw_text: '96%',
            value: '96',
            comparator: 'eq',
            unit: '%'
          }
        },
        existing_record: null,
        issue_code: null,
        detail: '新增候选；检测身份和结果尚未确认，未入库。',
        comment: ''
      },
      {
        id: 'cand-004',
        candidate_revision: 1,
        lifecycle: 'draft',
        detection_id: 'det-mock-004-empty',
        source_number: 'CoA-2025-0817',
        existing_detection_id: null,
        adopted_detection_id: null,
        test_date: '2026-10-10',
        corrections: {},
        allowed_actions: ['edit', 'withdraw'],
        location: {
          format: 'xlsx',
          section: 'Results',
          row: 5,
          column: 3
        },
        raw_fields: {
          test_item: '蛋白质含量 (Protein Content)',
          time_point: '24M',
          result: '',
          unit: 'mg/ml',
          source_number: 'CoA-2025-0817'
        },
        status: 'empty',
        record: {
          key: {
            project: 'IP166E',
            sample_type: 'DP',
            batch: 'E202506002A1',
            presentation: '1mg (0.5ml)',
            study: 'long_term',
            study_id: 'STB(C)-QC-IP166E-P-2-005',
            condition: '5C',
            month: 24,
            item: '蛋白质含量 (Protein Content)',
            method: '紫外 UV法',
            method_version: 'not_reported'
          },
          result: {
            status: 'blank',
            raw_text: '',
            value: null,
            comparator: null,
            unit: 'mg/ml'
          }
        },
        existing_record: null,
        issue_code: null,
        detail: '空白或占位，本次不会清除已确认结果。',
        comment: ''
      },
      {
        id: 'cand-005',
        candidate_revision: 1,
        lifecycle: 'draft',
        detection_id: 'det-mock-005-review',
        source_number: 'CoA-2025-0817',
        existing_detection_id: null,
        adopted_detection_id: null,
        test_date: '2026-10-10',
        corrections: {},
        allowed_actions: ['edit', 'withdraw'],
        location: {
          format: 'xlsx',
          section: 'Results',
          row: 6,
          column: 3
        },
        raw_fields: {
          test_item: '不溶性微粒 (≥10 μm)',
          time_point: '18M',
          result: '125',
          unit: '粒/支',
          source_number: 'CoA-2025-0817'
        },
        status: 'needs_review',
        record: {
          key: {
            project: 'IP166E',
            sample_type: 'DP',
            batch: 'E202506002A1',
            presentation: '1mg (0.5ml)',
            study: 'long_term',
            study_id: 'STB(C)-QC-IP166E-P-2-005',
            condition: '5C',
            month: 18,
            item: '不溶性微粒 (≥10 μm)',
            method: '光阻法',
            method_version: 'not_reported'
          },
          result: {
            status: 'numeric',
            raw_text: '125',
            value: '125',
            comparator: 'eq',
            unit: '粒/支'
          }
        },
        existing_record: null,
        issue_code: 'MULTIPLE_CANDIDATES_IN_FILE',
        detail: '同文件存在多项同槽位候选，检测批次/时间未唯一锚定，需人工核对。',
        comment: '请核对样品信息'
      }
    ],
    counts: {
      new: 1,
      same_value: 1,
      empty: 1,
      different_value: 1,
      needs_review: 1
    },
    can_commit: true
  };

  const MOCK_RESULT_PAGE = {
    project: 'IP166E',
    base_revision: 1,
    total: 1,
    offset: 0,
    limit: 100,
    cells: [
      {
        slot_id: 'IP166E:E202506002A1:lt:12M:sec_mono',
        slot_key: {
          project: 'IP166E',
          sample_type: 'DP',
          batch: 'E202506002A1',
          presentation: '1mg (0.5ml)',
          study: 'long_term',
          study_id: 'STB(C)-QC-IP166E-P-2-005',
          condition: '5C',
          month: 12,
          item: 'SEC-HPLC 单体纯度',
          method: 'SEC-HPLC',
          method_version: 'not_reported'
        },
        display: {
          result_version_id: 'rv-retest-12m-sec',
          detection_id: 'det-retest-2026-001',
          text: '98.8%',
          pending_confirmation: true,
          source_ids: ['doc-synthetic-example-xlsx']
        },
        adopted: {
          result_version_id: 'rv-orig-12m-sec',
          detection_id: 'det-orig-2025-089',
          text: '98.1%',
          pending_confirmation: false,
          source_ids: ['doc-initial-release-coax']
        },
        display_status: 'pending',
        upload_sequence: 1
      }
    ]
  };

  class V4ApiAdapter {
    constructor() {
      this.baseUrl = '/api/v1';
      this.authError = null;
      this.uploadIdempotencyKeys = new Map(); // file identity -> key
      this.mutationKeys = new Map(); // action:entityId -> idempotencyKey

      // Mode detection: URL param > window.__V4_MODE__ > HTTP protocol (real) > file:// (mock)
      let initialMode = 'mock';
      if (typeof window !== 'undefined') {
        const urlParams = window.location && window.location.search ? new URLSearchParams(window.location.search) : null;
        const modeParam = urlParams ? urlParams.get('mode') : null;
        if (modeParam === 'real' || modeParam === 'mock') {
          initialMode = modeParam;
        } else if (window.__V4_MODE__ === 'real' || window.__V4_MODE__ === 'mock') {
          initialMode = window.__V4_MODE__;
        } else if (window.location && window.location.protocol.startsWith('http')) {
          initialMode = 'real';
        }
      }
      this.mode = initialMode;
      this.currentActor = initialMode === 'real' ? null : MOCK_ACTOR;
    }

    generateIdempotencyKey(prefix = 'req', entityId = '') {
      if (entityId) {
        const actionKey = `${prefix}:${entityId}`;
        if (!this.mutationKeys.has(actionKey)) {
          const rnd = Math.random().toString(36).substring(2, 10);
          const ts = Date.now().toString(36);
          this.mutationKeys.set(actionKey, `${prefix}-${ts}-${rnd}`);
        }
        return this.mutationKeys.get(actionKey);
      }
      const rnd = Math.random().toString(36).substring(2, 10);
      const ts = Date.now().toString(36);
      return `${prefix}-${ts}-${rnd}`;
    }

    clearIdempotencyKey(prefix = 'req', entityId = '') {
      if (entityId) {
        this.mutationKeys.delete(`${prefix}:${entityId}`);
      }
    }

    getOrGenerateUploadKey(file) {
      if (!file) return this.generateIdempotencyKey('upload');
      const fileId = `${file.name}-${file.size}-${file.lastModified}`;
      if (!this.uploadIdempotencyKeys.has(fileId)) {
        this.uploadIdempotencyKeys.set(fileId, this.generateIdempotencyKey('up'));
      }
      return this.uploadIdempotencyKeys.get(fileId);
    }

    clearUploadKey(file) {
      if (!file) return;
      const fileId = `${file.name}-${file.size}-${file.lastModified}`;
      this.uploadIdempotencyKeys.delete(fileId);
    }

    setMode(newMode) {
      if (newMode !== 'mock' && newMode !== 'real') return;
      this.mode = newMode;
      this.authError = null;
      if (this.mode === 'real') {
        this.currentActor = null; // NEVER fallback to mock actor in real mode!
        if (window.v4ResultCell) {
          window.v4ResultCell.clearCells();
        }
      } else {
        this.currentActor = MOCK_ACTOR;
        if (window.v4ResultCell) {
          window.v4ResultCell.clearCells();
          window.v4ResultCell.initDefaultCells();
        }
      }
    }

    async getActor() {
      if (this.mode === 'mock') {
        this.authError = null;
        this.currentActor = MOCK_ACTOR;
        return this.currentActor;
      }

      // Real mode: fetch from /api/v1/me, if failed, MUST NOT fallback to mock actor!
      try {
        const res = await fetch(`${this.baseUrl}/me`);
        if (!res.ok) {
          const errJson = await res.json().catch(() => ({}));
          throw new ApiClientError(errJson.code || 'AUTH_FAILED', errJson.message || `身份认证失败: HTTP ${res.status}`, errJson.details);
        }
        this.currentActor = await res.json();
        this.authError = null;
        return this.currentActor;
      } catch (err) {
        this.currentActor = null; // Explicitly no permissions
        this.authError = err.message || '网络连接失败，无法获取真实用户权限';
        throw err;
      }
    }

    async sourceRequest(path, options = {}) {
      if (this.mode !== 'real') throw new Error('来源接口仅支持真实模式');
      const response = await fetch(this.baseUrl + path, options);
      const body = await response.json();
      if (!response.ok) {
        const error = new ApiClientError(body.code || 'HTTP_ERROR',body.message || `HTTP ${response.status}`,body.details || {});
        error.status = response.status;
        throw error;
      }
      return body;
    }
    getConfiguration(project) {
      return this.sourceRequest(`/projects/${encodeURIComponent(project)}/configuration`);
    }
    listPreviews(project, offset = 0) {
      return this.sourceRequest(`/projects/${encodeURIComponent(project)}/previews?offset=${offset}&limit=20`);
    }
    changeConfiguration(project, command) {
      return this.sourceRequest(`/projects/${encodeURIComponent(project)}/configuration`, {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(command)});
    }
    getSourceImports(project) { return this.sourceRequest(`/projects/${encodeURIComponent(project)}/source-imports`); }
    getSourceTables(id) { return this.sourceRequest(`/source-imports/${encodeURIComponent(id)}`); }
    importSourcePair(project, word, pack, key) {
      const form = new FormData(); form.append('file', word); form.append('package', pack);
      return this.sourceRequest(`/projects/${encodeURIComponent(project)}/source-imports`, {
        method:'POST', headers:{'Idempotency-Key':key}, body:form});
    }
    sourcePreview(id, command) {
      return this.sourceRequest(`/source-imports/${encodeURIComponent(id)}/previews`, {
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(command)});
    }

    async getProjects() {
      if (this.mode === 'mock') {
        return [{ project: 'IP166E', revision: 1 }];
      }
      const res = await fetch(`${this.baseUrl}/projects`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'PROJECTS_FAILED', errJson.message || `获取项目列表失败: HTTP ${res.status}`);
      }
      return await res.json();
    }

    async createPreview(file, project, context, idempotencyKey) {
      if (this.mode === 'mock') {
        const preview = JSON.parse(JSON.stringify(MOCK_PREVIEW));
        if (file && file.name) {
          preview.filename = file.name;
        }
        preview.project = project || 'IP166E';
        return preview;
      }

      // Real mode: POST multipart with Idempotency-Key header and context
      const key = idempotencyKey || this.getOrGenerateUploadKey(file);
      const formData = new FormData();
      formData.append('file', file);

      // Context must be a JSON string validating against ImportContext
      const importContext = context && typeof context === 'object'
        ? { role: context.role || 'stability_source', defaults: context.defaults || context }
        : { role: 'stability_source', defaults: {} };

      formData.append('context', JSON.stringify(importContext));

      const res = await fetch(`${this.baseUrl}/projects/${encodeURIComponent(project)}/previews`, {
        method: 'POST',
        headers: {
          'Idempotency-Key': key
        },
        body: formData
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'PREVIEW_FAILED', errJson.message || `预览生成失败: HTTP ${res.status}`, errJson.details);
      }
      return await res.json();
    }

    async getPreview(previewId) {
      if (this.mode === 'mock') {
        return JSON.parse(JSON.stringify(MOCK_PREVIEW));
      }

      const res = await fetch(`${this.baseUrl}/previews/${encodeURIComponent(previewId)}`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'PREVIEW_GET_FAILED', errJson.message || `获取预览失败: HTTP ${res.status}`, errJson.details);
      }
      return await res.json();
    }

    async mapItems(previewId, command) {
      if (this.mode !== 'real') throw new Error('批量映射仅适用于真实预览');
      return this.sourceRequest(`/previews/${encodeURIComponent(previewId)}/mapping`, {
        method:'PATCH', headers:{'Content-Type':'application/json'},body:JSON.stringify(command)});
    }

    async editCandidate(candidateId, editCommand) {
      if (this.mode === 'mock') {
        this.clearIdempotencyKey('edit', candidateId);
        return { project: 'IP166E', base_revision: 1, result_version_ids: [], pending_confirmation_ids: [] };
      }

      const res = await fetch(`${this.baseUrl}/candidates/${encodeURIComponent(candidateId)}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(editCommand)
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        // Retain idempotency key on failure to allow retry
        throw new ApiClientError(errJson.code || 'CANDIDATE_EDIT_FAILED', errJson.message || `修正候选失败: HTTP ${res.status}`, errJson.details);
      }
      this.clearIdempotencyKey('edit', candidateId);
      return await res.json();
    }

    async withdrawCandidate(candidateId, withdrawCommand) {
      if (this.mode === 'mock') {
        return { project: 'IP166E', base_revision: 1, result_version_ids: [], pending_confirmation_ids: [] };
      }

      const res = await fetch(`${this.baseUrl}/candidates/${encodeURIComponent(candidateId)}/withdraw`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(withdrawCommand)
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'WITHDRAW_FAILED', errJson.message || `撤回候选失败: HTTP ${res.status}`, errJson.details);
      }
      return await res.json();
    }

    async confirmPreview(previewId, confirmCommand) {
      if (this.mode === 'mock') {
        const hasRetest = confirmCommand.items && confirmCommand.items.some(i => i.intent === 'retest' || i.intent === 'repeat_source');
        this.clearIdempotencyKey('confirm', previewId);
        return {
          project: 'IP166E',
          base_revision: 2,
          result_version_ids: ['rv-mock-confirmed-001'],
          pending_confirmation_ids: hasRetest ? ['rv-mock-pending-001'] : []
        };
      }

      const res = await fetch(`${this.baseUrl}/previews/${encodeURIComponent(previewId)}/confirm`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(confirmCommand)
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        // Retain idempotency key on failure to allow retry
        throw new ApiClientError(errJson.code || 'CONFIRM_FAILED', errJson.message || `提交确认失败: HTTP ${res.status}`, errJson.details);
      }
      this.clearIdempotencyKey('confirm', previewId);
      return await res.json();
    }

    async reviewResult(versionId, reviewCommand) {
      if (this.mode === 'mock') {
        return { project: 'IP166E', base_revision: 2, result_version_ids: [versionId], pending_confirmation_ids: [] };
      }

      const res = await fetch(`${this.baseUrl}/results/${encodeURIComponent(versionId)}/review`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(reviewCommand)
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'REVIEW_FAILED', errJson.message || `复核失败: HTTP ${res.status}`, errJson.details);
      }
      return await res.json();
    }

    async getResultCells(project, filters = {}, offset = 0, limit = 100) {
      if (this.mode === 'mock') {
        return JSON.parse(JSON.stringify(MOCK_RESULT_PAGE));
      }

      let reqOffset = offset;
      let reqLimit = limit;
      const queryFilters = {};

      if (typeof filters === 'number') {
        reqOffset = filters;
        if (typeof offset === 'number' && offset !== 0) reqLimit = offset;
      } else if (filters && typeof filters === 'object') {
        if ('offset' in filters && typeof filters.offset === 'number') {
          reqOffset = filters.offset;
        }
        if ('limit' in filters && typeof filters.limit === 'number') {
          reqLimit = filters.limit;
        }
        for (const [k, v] of Object.entries(filters)) {
          if (k !== 'offset' && k !== 'limit' && v != null && v !== '') {
            queryFilters[k] = v;
          }
        }
      }

      const params = new URLSearchParams();
      params.append('offset', String(reqOffset));
      params.append('limit', String(reqLimit));
      Object.entries(queryFilters).forEach(([k, v]) => {
        params.append(k, String(v));
      });

      const res = await fetch(`${this.baseUrl}/projects/${encodeURIComponent(project)}/result-cells?${params.toString()}`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'FETCH_CELLS_FAILED', errJson.message || `获取结果单元格失败: HTTP ${res.status}`);
      }
      return await res.json();
    }

    async getPendingResults(project) {
      if (this.mode === 'mock') {
        return [];
      }
      const res = await fetch(`${this.baseUrl}/projects/${encodeURIComponent(project)}/pending-results`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'FETCH_PENDING_FAILED', errJson.message || `获取待审结果失败: HTTP ${res.status}`);
      }
      return await res.json();
    }

    async getSource(sourceId) {
      if (this.mode === 'mock') {
        return {
          source_id: sourceId,
          source_hash: 'mock-hash-582e67',
          upload_sequence: 1,
          filename: 'synthetic-example.xlsx',
          coordinates: { section: 'Results', row: 2, column: 3 },
          raw_text: '98.8%'
        };
      }
      const res = await fetch(`${this.baseUrl}/sources/${encodeURIComponent(sourceId)}`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'FETCH_SOURCE_FAILED', errJson.message || `获取来源记录失败: HTTP ${res.status}`);
      }
      return await res.json();
    }

    async getHistory(project) {
      if (this.mode === 'mock') {
        return [];
      }
      const res = await fetch(`${this.baseUrl}/projects/${encodeURIComponent(project)}/history`);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'FETCH_HISTORY_FAILED', errJson.message || `获取操作历史失败: HTTP ${res.status}`);
      }
      return await res.json();
    }

    async requestExport(exportCommand) {
      if (this.mode === 'mock') {
        return {
          export_id: 'export_mock_001',
          project: exportCommand.project || 'IP166E',
          base_revision: exportCommand.base_revision || 1,
          state: 'verified',
          filename: 'mock-results.xlsx',
          download_url: '/api/v1/exports/export_mock_001/file'
        };
      }

      const res = await fetch(`${this.baseUrl}/exports`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(exportCommand)
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'EXPORT_FAILED', errJson.message || `生成导出任务失败: HTTP ${res.status}`, errJson.details);
      }
      return await res.json();
    }

    async downloadExport(exportId, filename = 'results.xlsx') {
      if (this.mode === 'mock') {
        toast('Mock 模式模拟下载 Excel 文件');
        return;
      }

      const url = `${this.baseUrl}/exports/${encodeURIComponent(exportId)}/file`;
      const res = await fetch(url);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new ApiClientError(errJson.code || 'DOWNLOAD_FAILED', errJson.message || `下载导出文件失败: HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      if (typeof a.remove === 'function') {
        a.remove();
      } else if (a.parentNode) {
        a.parentNode.removeChild(a);
      }
      URL.revokeObjectURL(blobUrl);
    }

    async exportDataset(project, options = {}) {
      const prj = project || (typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E');
      const baseRev = options.base_revision != null ? options.base_revision : (window.v4ResultCell?.currentBaseRevision ?? 0);
      const filters = { ...(options.filters || {}) };
      if (options.batch) filters.batch = options.batch;
      if (options.condition) filters.condition = options.condition;
      return await this.exportProject({
        project: prj,
        base_revision: baseRev,
        filters: filters,
        orientation: options.orientation || 'items'
      });
    }

    async exportProject(options = {}) {
      const prj = options.project || (typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E');
      const baseRev = options.base_revision != null ? options.base_revision : (window.v4ResultCell?.currentBaseRevision ?? 0);
      const orientation = options.orientation || (typeof v3Orientation !== 'undefined' ? v3Orientation : 'items');
      const filters = options.filters || {};

      const cmd = {
        project: prj,
        base_revision: baseRev,
        filters: filters,
        orientation: orientation
      };

      try {
        const res = await this.requestExport(cmd);
        if (res && res.export_id) {
          await this.downloadExport(res.export_id, res.filename || `${prj}-results.xlsx`);
          if (typeof toast === 'function') {
            toast(`Excel 导出已完成 (${res.filename || 'results.xlsx'})`);
          }
        }
        return res;
      } catch (err) {
        if (err.code === 'stale_revision' || (err.message && err.message.includes('409'))) {
          if (typeof toast === 'function') {
            toast('基础快照版本已变更 (409 Conflict)，请刷新页面核对最新数据后再导出。');
          }
        } else {
          if (typeof toast === 'function') {
            toast(err.message || '导出生成失败');
          }
        }
        throw err;
      }
    }

    can(capability) {
      if (this.mode === 'real' && !this.currentActor) {
        return false;
      }
      return Boolean(this.currentActor && this.currentActor.capabilities && this.currentActor.capabilities.includes(capability));
    }
  }

  global.v4Api = new V4ApiAdapter();

  document.addEventListener('DOMContentLoaded', () => {
    if (global.v4Api && global.v4Api.mode === 'real') {
      if (typeof switchMainTab === 'function') {
        switchMainTab = function(tabId) {
          if(global.v5Review&&!global.v5Review.leave())return;
          if (!['intake','ledger','comparability','reports'].includes(tabId)) return;
          activeWorkspaceTab = tabId;
          document.querySelectorAll('.nav-tab').forEach((button,index) => button.classList.toggle('active',index === ['intake','ledger','comparability','reports'].indexOf(tabId)));
          document.querySelectorAll('.tab-page').forEach(page => page.classList.toggle('active',page.id === 'tab-' + tabId));
          if (tabId === 'ledger') return global.v5Review?global.v5Review.enterLedger(currentProjectKey):global.v4ResultCell?.loadProjectCells(currentProjectKey);
          global.v5Review?.setScene('upload');
          if (['comparability','reports'].includes(tabId)) return global.v014Research?.enter(tabId,currentProjectKey);
        };
      }
      if (typeof selectProject === 'function') {
        selectProject = function(id, announce = true) {
          const project = projectRegistry.find(p => p.id === id);
          if (!project) return;
          currentProjectKey = id; hubPreferences.current = id;
          hubPreferences.recent = [id,...hubPreferences.recent.filter(p => p !== id)].slice(0,20);
          saveHubPreferences(); updateWorkspaceFavorite();
          document.getElementById('workspaceName').textContent = project.name === id ? id : `${id} · ${project.name}`;
          const intakeLabel = document.getElementById('intakeProjectVal');
          if (intakeLabel) intakeLabel.textContent = project.name;
          const address = new URL(window.location.href); address.searchParams.set('project',id);
          window.history.replaceState(null,'',address);
          document.querySelector('main.main-content').hidden = false;
          document.getElementById('scopedWorkspace').hidden = true;
          document.querySelectorAll('.nav-tab').forEach((button,index) => {button.hidden = false; button.disabled = false;});
          const favorite = document.getElementById('workspaceFavorite'); if (favorite) {favorite.disabled=false; favorite.hidden=false;}
          global.v4Intake?.onContextChanged();
          global.v4ResultCell?.clearCells(); global.v4ResultCell?.enhanceLedgerTable();
          switchMainTab('intake'); document.getElementById('projectCenter').close();
          if (announce) toast(`已进入 ${id}`);
        };
      }
      global.v4ResultCell?.enhanceLedgerTable();
      const bar = document.createElement('div');
      bar.id = 'v43Connection';
      bar.style.cssText = 'padding:10px 20px;background:#eef4fa;border-bottom:1px solid #cbd5e1;font-size:12px;overflow-wrap:anywhere';
      document.body.prepend(bar);
      const text = document.createElement('span');
      const retry = document.createElement('button');
      retry.textContent = '重新连接 / 刷新'; retry.className = 'btn btn-outline btn-sm';
      retry.style.marginLeft = '12px';
      bar.append(text, retry);
      global.v4Api.reconnect = async () => {
        retry.disabled = true; text.textContent = '正在连接 V4.5 本地服务…';
        try {
          await global.v4Api.getActor();
          const runtime = await global.v4Api.sourceRequest('/runtime');
          const projects = await global.v4Api.getProjects();
          let preferred = null;
          if (typeof projectRegistry !== 'undefined') {
            const allowed = new Set(projects.map(p => p.project));
            projectRegistry = projects.map(p => ({
              id:p.project,name:p.name || p.project,type:'未配置产品类型',stage:'待确认',status:'进行中',modules:['intake','ledger','comparability','reports']}));
            projectRegistry.forEach(p => { p.modules = ['intake','ledger','comparability','reports']; });
            const requested = new URLSearchParams(window.location.search).get('project');
            preferred = allowed.has(requested) ? requested : null;
            hubPreferences.favorites=hubPreferences.favorites.filter(id=>allowed.has(id));
            hubPreferences.recent=hubPreferences.recent.filter(id=>allowed.has(id));
            hubPreferences.current=preferred||'';
            if (preferred && preferred !== currentProjectKey) selectProject(preferred, false);
            if (typeof renderProjectDirectory === 'function') renderProjectDirectory();
          }
          text.textContent = `V${runtime.version} 候选 · 代码 ${(runtime.code_commit||'unknown').slice(0,12)}${runtime.code_dirty?'（含未提交修复）':''} · ${runtime.actor} (${runtime.role}) · 数据库：${runtime.data_home}`;
          bar.style.background = '#ecfdf5';
          const prj = typeof currentProjectKey !== 'undefined' ? currentProjectKey : 'IP166E';
          if (preferred) {
            if(!global.v5Review)await global.v4ResultCell?.loadProjectCells(prj);
            await global.v4Intake?.refreshRecent();
            const view=new URLSearchParams(window.location.search).get('view');if(['ledger','comparability','reports'].includes(view)&&typeof switchMainTab==='function')switchMainTab(view);
          } else global.v4Intake?.goUploadHome();
        } catch (err) {
          text.textContent = `连接失败：${err.message}。请使用对应版本启动器，再点重新连接。`;
          const details=bar.querySelector('details');if(details)details.open=true;
          bar.style.background = '#fef2f2';
          if (global.v4ResultCell) {
            global.v4ResultCell.clearCells(); global.v4ResultCell.loadError = err.message;
            global.v4ResultCell.enhanceLedgerTable();
          }
        } finally { retry.disabled = false; }
      };
      retry.onclick = () => global.v4Api.reconnect();
      global.v4Api.reconnect();
    }
  });
})(typeof window !== 'undefined' ? window : this);
