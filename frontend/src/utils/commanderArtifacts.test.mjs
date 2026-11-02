import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  artifactPreviewTarget,
  artifactTitle,
  externalArtifactLink,
  isDocumentArtifact,
} from './commanderArtifacts.ts'

/** 后端指挥官产物的真实形状（v3.52 修复「点开产物变成前端 404 页」） */
const documentArtifact = {
  type: 'document',
  title: '本周经营复盘.pptx',
  filename: '本周经营复盘.pptx',
  format: 'pptx',
  session_id: 'sess-1',
  attachment_id: 'att-1',
  url: '/chat/sessions/sess-1/attachments/att-1/download',
  preview_url: '/chat/sessions/sess-1/attachments/att-1/preview',
}

test('文档产物不渲染成浏览器链接', () => {
  assert.equal(externalArtifactLink(documentArtifact), '')
  assert.equal(isDocumentArtifact(documentArtifact), true)
})

test('文档产物给出应用内预览三要素', () => {
  assert.deepEqual(artifactPreviewTarget(documentArtifact), {
    session_id: 'sess-1',
    attachment_id: 'att-1',
    filename: '本周经营复盘.pptx',
  })
})

test('联网资料等真外链仍然可点', () => {
  const link = { type: 'link', title: '某资讯', url: 'https://example.com/a?x=1' }
  assert.equal(isDocumentArtifact(link), false)
  assert.equal(externalArtifactLink(link), 'https://example.com/a?x=1')
  assert.equal(artifactPreviewTarget(link), null)
})

test('危险协议与空值一律不给链接', () => {
  assert.equal(externalArtifactLink({ url: 'javascript:alert(1)' }), '')
  assert.equal(externalArtifactLink({ url: '//example.com/a' }), '')
  assert.equal(externalArtifactLink({}), '')
})

test('标题按 title → name → filename → url 回退', () => {
  assert.equal(artifactTitle({ title: '甲', filename: '乙' }), '甲')
  assert.equal(artifactTitle({ name: '丙', filename: '乙' }), '丙')
  assert.equal(artifactTitle({ filename: '乙' }), '乙')
  assert.equal(artifactTitle({ url: 'https://example.com/x' }), 'https://example.com/x')
  assert.equal(artifactTitle({}), '未命名产物')
})
