/**
 * Test Case Viewer – load, edit, and save Excel test cases.
 * Used in pipeline Stage 4 (Case Review).
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Button, Tabs, Table, Select, message, Space, Tag, Modal, Input,
  Tooltip, Badge, Divider, Upload,
} from 'antd';
import {
  ReloadOutlined,
  SaveOutlined, FileTextOutlined,
  HistoryOutlined, FolderOpenOutlined, UploadOutlined,
  CloseOutlined,
} from '@ant-design/icons';
import { loadTestFile, saveTestFile, saveTestReview, listTestFiles, browseDirectory, type BrowseResult } from '../../services/api';
import { useUiStore } from '../../stores/uiStore';
import './TestCaseViewer.css';

const { TextArea } = Input;

interface SheetData {
  headers: string[];
  rows: Record<string, string>[];
}

const TestCaseViewer: React.FC<{ embedded?: boolean }> = ({ embedded }) => {
  const { setTestCaseData } = useUiStore();

  const [files, setFiles] = useState<Array<{ name: string; path: string }>>([]);
  const [currentFile, setCurrentFile] = useState('');
  const [customDir, setCustomDir] = useState('');
  const [testhubDir, setTesthubDir] = useState('');
  const [sheets, setSheets] = useState<Record<string, SheetData>>({});
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheet, setActiveSheet] = useState('');
  const [loading, setLoading] = useState(false);
  const [changedCases, setChangedCases] = useState<Array<{sheet: string; row: number; case_id: string; field: string; old_val: string; new_val: string}>>([]);
  const [reviewLog, setReviewLog] = useState<string[]>([]);
  const [logVisible, setLogVisible] = useState(false);

  // Editable cell tracking
  const editedCells = useRef<Set<string>>(new Set());

  // Directory browse state
  const [browseVisible, setBrowseVisible] = useState(false);
  const [browseResult, setBrowseResult] = useState<BrowseResult | null>(null);
  const [browsePath, setBrowsePath] = useState('');

  // Load files list on mount
  useEffect(() => {
    loadFiles();
  }, []);

  const loadFiles = async (dir?: string) => {
    try {
      const result = await listTestFiles(dir);
      setFiles(result.files || []);
      setTesthubDir(result.testhub_dir || '');
      if (result.files?.length && !currentFile) {
        handleLoadFile(result.files[0].name, dir);
      } else if (!result.files?.length) {
        setCurrentFile('');
        setSheets({});
        setSheetNames([]);
        setActiveSheet('');
      }
    } catch {
      message.error('加载用例文件列表失败');
    }
  };

  const handleLoadFile = async (filename: string, dir?: string) => {
    if (!filename) return;
    setLoading(true);
    try {
      const data = await loadTestFile(filename, dir || customDir || undefined);
      setCurrentFile(data.filename);
      setSheets(data.sheets as Record<string, SheetData>);
      setSheetNames(data.sheet_names || []);
      setActiveSheet(data.sheet_names?.[0] || '');
      setChangedCases([]);
      addLog('load', `Loaded: ${filename}`, filename);

      // Build summary for pipeline Stage 5
      buildAndSaveSummary(data.sheets as Record<string, SheetData>);
    } catch {
      message.error('加载测试文件失败');
    }
    setLoading(false);
  };

  const handleDirChange = () => {
    const dir = customDir.trim();
    setCurrentFile('');
    loadFiles(dir || undefined);
  };

  const handleResetDir = () => {
    setCustomDir('');
    setCurrentFile('');
    loadFiles();
  };

  // ── Directory browse handlers ──────────────────────
  const openBrowse = useCallback(async (path?: string) => {
    try {
      const result = await browseDirectory(path || '', false);
      setBrowseResult(result);
      setBrowsePath(result.current);
      setBrowseVisible(true);
    } catch {
      message.error('加载目录失败');
    }
  }, []);

  const handleBrowseDir = useCallback((dirPath: string) => {
    openBrowse(dirPath);
  }, [openBrowse]);

  const handleBrowseParent = useCallback(() => {
    if (browseResult?.parent) {
      openBrowse(browseResult.parent);
    }
  }, [openBrowse, browseResult]);

  const handleSelectDir = useCallback(() => {
    setCustomDir(browsePath);
    setBrowseVisible(false);
    // Auto-load files from selected directory
    setCurrentFile('');
    loadFiles(browsePath);
  }, [browsePath, loadFiles]);

  /** Find column index by header name (case-insensitive partial match) */
  const findCol = (headers: string[], keywords: string[]): number => {
    return headers.findIndex(h => keywords.some(kw => h.toLowerCase().includes(kw.toLowerCase())));
  };

  const buildAndSaveSummary = (sheetsData: Record<string, SheetData>) => {
    const lines: string[] = [];
    for (const [sname, sdata] of Object.entries(sheetsData)) {
      const headers = sdata.headers || [];
      const rows = sdata.rows || [];
      if (rows.length === 0) continue;

      // Use column name matching instead of hardcoded indices
      const idCol = findCol(headers, ['用例id', 'caseid', 'case_id', 'id']);
      const nameCol = findCol(headers, ['用例名称', '名称', 'name', 'title']);
      const priorityCol = findCol(headers, ['优先级', 'priority']);
      const stepsCol = findCol(headers, ['测试步骤', '步骤', 'steps', '操作']);
      const expectedCol = findCol(headers, ['预期结果', '预期', 'expected', '期望']);

      // Skip summary sheets that don't contain case IDs (e.g., "模块" overview sheet)
      if (idCol < 0 || nameCol < 0) {
        console.log(`[TestCaseViewer] Skipping sheet "${sname}" — no case ID or name column found`);
        continue;
      }

      lines.push(`## Sheet: ${sname}`);
      for (const row of rows) {
        const id = row[headers[idCol]] || '';
        const name = row[headers[nameCol]] || '';
        const priority = priorityCol >= 0 ? (row[headers[priorityCol]] || '') : '';
        const steps = stepsCol >= 0 ? (row[headers[stepsCol]] || '') : '';
        const expected = expectedCol >= 0 ? (row[headers[expectedCol]] || '') : '';
        if (id && name) {
          lines.push(`- [${id}] ${name} (${priority})\n  Steps: ${steps}\n  Expected: ${expected}`);
        }
      }
    }
    setTestCaseData(lines.join('\n'));
  };

  const addLog = (action: string, comment: string, filename?: string, sheet?: string, caseId?: string) => {
    const entry = `[${new Date().toLocaleTimeString()}] ${action}: ${comment}`;
    setReviewLog(prev => [...prev.slice(-99), entry]);
    saveTestReview({
      action, comment, filename: filename || currentFile, sheet: sheet || '', case_id: caseId || '', details: '',
    }).catch(() => {});
  };

  // Handle cell edit
  const handleCellEdit = (rowIndex: number, key: string, newValue: string, row: Record<string, any>) => {
    if (!activeSheet || !sheets[activeSheet]) return;

    const oldValue = row[key] || '';
    if (oldValue === newValue) return;

    // Update sheet data
    const updatedSheets = { ...sheets };
    const rows = [...updatedSheets[activeSheet].rows];
    rows[rowIndex] = { ...rows[rowIndex], [key]: newValue };
    updatedSheets[activeSheet] = { ...updatedSheets[activeSheet], rows };
    setSheets(updatedSheets);

    // Track change
    const activeHeaders = sheets[activeSheet].headers;
    const caseIdCol = findCol(activeHeaders, ['用例id', 'caseid', 'case_id', 'id']);
    const caseId = caseIdCol >= 0 ? (row[activeHeaders[caseIdCol]] || `row_${rowIndex}`) : `row_${rowIndex}`;
    setChangedCases(prev => {
      const existing = prev.find(c => c.sheet === activeSheet && c.row === rowIndex && c.field === key);
      if (existing) {
        return prev.map(c => c === existing ? { ...c, new_val: newValue } : c);
      }
      return [...prev, { sheet: activeSheet, row: rowIndex, case_id: caseId, field: key, old_val: oldValue, new_val: newValue }];
    });

    editedCells.current.add(`${activeSheet}_${rowIndex}_${key}`);
    addLog('edit', `${caseId}: ${key} "${oldValue}" → "${newValue}"`, undefined, activeSheet, caseId);
  };

  // Save changes back to Excel
  const handleSave = async () => {
    if (!currentFile) return;
    setLoading(true);
    try {
      await saveTestFile({ filename: currentFile, sheets });
      message.success('测试用例已保存');
      setChangedCases([]);
      addLog('save', `Saved ${changedCases.length} changes to ${currentFile}`);
    } catch {
      message.error('保存失败');
    }
    setLoading(false);
  };

  // Build table columns from headers
  const buildColumns = (headers: string[]) => {
    return headers.map((h, idx) => ({
      title: h,
      dataIndex: h,
      key: h,
      width: idx === 0 ? 130 : idx <= 2 ? 100 : idx >= 4 ? 280 : 150,
      ellipsis: true,
      render: (text: string, record: any, rowIndex: number) => {
        const cellKey = `${activeSheet}_${rowIndex}_${h}`;
        const isEdited = editedCells.current.has(cellKey);

        return (
          <Tooltip title={text || '(空)'}>
            <div
              style={{
                cursor: 'text',
                minHeight: 22,
                background: isEdited ? '#fff7e6' : undefined,
                padding: '2px 4px',
                borderRadius: 2,
              }}
              contentEditable={false}
              onDoubleClick={(e) => {
                const el = e.currentTarget;
                const origText = text;
                el.contentEditable = 'true';
                el.focus();
                const onBlur = () => {
                  el.contentEditable = 'false';
                  const newText = el.textContent || '';
                  if (newText !== origText) {
                    handleCellEdit(rowIndex, h, newText, record as any);
                  }
                  el.removeEventListener('blur', onBlur);
                };
                el.addEventListener('blur', onBlur);
              }}
            >
              {text || <span style={{ color: '#ccc' }}>(空)</span>}
            </div>
          </Tooltip>
        );
      },
    }));
  };

  const currentSheetData = sheets[activeSheet];
  const columns = currentSheetData ? buildColumns(currentSheetData.headers) : [];
  const dataSource: Record<string, unknown>[] = currentSheetData?.rows.map((r, i) => ({ ...r, _key: i })) || [];

  return (
    <div className={`testcase-viewer ${embedded ? 'embedded' : ''}`}>
      {/* Header */}
      <div className="testcase-header">
        <h3>用例检视</h3>
        <Space wrap>
          <Input.Group compact>
            <Input
              prefix={<FolderOpenOutlined />}
              value={customDir}
              onChange={e => setCustomDir(e.target.value)}
              onPressEnter={handleDirChange}
              placeholder="输入用例目录路径（留空使用默认）"
              style={{ width: 260 }}
              allowClear
              onClear={handleResetDir}
            />
            <Tooltip title="浏览选择目录">
              <Button icon={<FolderOpenOutlined />} onClick={() => openBrowse(customDir || undefined)} />
            </Tooltip>
            {customDir && (
              <Button icon={<CloseOutlined />} onClick={handleResetDir} />
            )}
          </Input.Group>
          <Button onClick={handleDirChange} size="small">加载</Button>
          <Button onClick={handleResetDir} size="small" disabled={!customDir}>默认</Button>
          <Divider type="vertical" />

          {files.length > 0 ? (
            <Select
              value={currentFile}
              onChange={v => handleLoadFile(v)}
              style={{ width: 250 }}
              options={files.map(f => ({ value: f.name, label: f.name }))}
              placeholder="选择测试文件..."
            />
          ) : (
            <span style={{ color: '#999' }}>当前无额外用例</span>
          )}
          <Tooltip title="刷新文件列表">
            <Button icon={<ReloadOutlined />} onClick={() => loadFiles(customDir || undefined)} size="small" />
          </Tooltip>
          <Divider type="vertical" />

          <Badge count={changedCases.length} size="small" offset={[-4, 4]}>
            <Button
              icon={<SaveOutlined />}
              onClick={handleSave}
              disabled={changedCases.length === 0}
              size="small"
              type={changedCases.length > 0 ? 'primary' : 'default'}
            >
              保存修改 ({changedCases.length})
            </Button>
          </Badge>

          <Divider type="vertical" />

          <Tooltip title="操作日志">
            <Badge count={reviewLog.length} size="small">
              <Button
                icon={<HistoryOutlined />}
                onClick={() => setLogVisible(true)}
                size="small"
              />
            </Badge>
          </Tooltip>
        </Space>
      </div>

      {/* Sheet tabs */}
      {sheetNames.length > 0 && (
        <Tabs
          activeKey={activeSheet}
          onChange={(key) => {
            setActiveSheet(key);
            addLog('switch_sheet', `Switched to sheet: ${key}`, undefined, key);
          }}
          size="small"
          items={sheetNames.map(name => ({
            key: name,
            label: name,
          }))}
        />
      )}

      {/* Data table */}
      <div className="testcase-table">
        {currentSheetData ? (
          <Table
            columns={columns}
            dataSource={dataSource}
            rowKey="_key"
            size="small"
            scroll={{ x: 900, y: 'calc(100vh - 320px)' }}
            pagination={false}
            loading={loading}
            bordered
          />
        ) : (
          <div className="testcase-empty">
            {loading ? '加载中...' : '选择测试文件以查看用例'}
          </div>
        )}
      </div>

      {/* Changed cases summary */}
      {changedCases.length > 0 && (
        <div className="testcase-changes">
          <h4>变更记录 ({changedCases.length})</h4>
          <div className="changes-list">
            {changedCases.slice(0, 10).map((c, i) => (
              <Tag key={i} color="orange">
                [{c.sheet}] {c.case_id}: {c.field} "{c.old_val}" → "{c.new_val}"
              </Tag>
            ))}
            {changedCases.length > 10 && <Tag>+{changedCases.length - 10} more</Tag>}
          </div>
        </div>
      )}

      {/* Directory browse modal */}
      <Modal
        title="选择用例目录"
        open={browseVisible}
        onCancel={() => setBrowseVisible(false)}
        footer={[
          <Button key="cancel" onClick={() => setBrowseVisible(false)}>取消</Button>,
          <Button key="select" type="primary" onClick={handleSelectDir}>选择此目录</Button>,
        ]}
        width={550}
      >
        <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Button size="small" onClick={handleBrowseParent} disabled={!browseResult?.parent}>
            上级目录
          </Button>
          <span style={{ fontSize: 12, color: '#666', wordBreak: 'break-all' }}>
            {browseResult?.current || ''}
          </span>
        </div>
        <div style={{ maxHeight: 320, overflow: 'auto' }}>
          {browseResult?.dirs?.map(dir => (
            <div
              key={dir.path}
              onClick={() => handleBrowseDir(dir.path)}
              style={{ cursor: 'pointer', padding: '4px 8px', borderRadius: 4,
                       display: 'flex', alignItems: 'center', gap: 8 }}
            >
              <FolderOpenOutlined style={{ color: '#faad14' }} />
              <span style={{ fontSize: 13 }}>{dir.name}</span>
            </div>
          ))}
          {(!browseResult?.dirs || browseResult.dirs.length === 0) && (
            <div style={{ color: '#999', fontSize: 12, padding: 8 }}>此目录下无子目录</div>
          )}
        </div>
        {browseResult?.files && browseResult.files.length > 0 && (
          <div style={{ marginTop: 8, fontSize: 11, color: '#888' }}>
            此目录包含 {browseResult.files.length} 个文件
          </div>
        )}
      </Modal>

      {/* Operation log modal */}
      <Modal
        title="操作日志"
        open={logVisible}
        onCancel={() => setLogVisible(false)}
        footer={<Button onClick={() => setLogVisible(false)}>关闭</Button>}
        width={700}
      >
        <div className="review-log-content">
          {reviewLog.map((entry, i) => (
            <div key={i} className="log-entry">{entry}</div>
          ))}
          {reviewLog.length === 0 && <div style={{ color: '#999' }}>暂无操作记录</div>}
        </div>
      </Modal>
    </div>
  );
};

export default TestCaseViewer;
