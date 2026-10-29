"""把 SubmitPage 里内联的 SegmentTimeline 删除，改用共享组件。"""
import io
import os
import sys

path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'web', 'src', 'pages', 'SubmitPage.tsx')
with io.open(path, encoding='utf-8') as f:
    lines = f.readlines()

START_MARK = '---------------- 切片审核：时间轴 + 逐段结论 ----------------'
END_MARK = "const ORDER = ['meta', 'frames', 'vision', 'text', 'rules', 'decide']"

start_idx = [i for i, ln in enumerate(lines) if START_MARK in ln]
end_idx = [i for i, ln in enumerate(lines) if END_MARK in ln]
print(f'起标记 {len(start_idx)} 次，止标记 {len(end_idx)} 次')
if len(start_idx) != 1 or len(end_idx) != 1:
    print('标记不唯一，中止')
    sys.exit(2)

s, e = start_idx[0], end_idx[0]
if e <= s:
    print('顺序异常，中止')
    sys.exit(2)

block = lines[s:e]
text = ''.join(block)
assert 'function SegmentTimeline' in text, '待删区间不含组件'
assert 'const SEG_TONE' in text, '待删区间不含 SEG_TONE'

print(f'删除第 {s + 1}-{e} 行（共 {len(block)} 行）')
print('  首行:', block[0].rstrip()[:70])
print('  末行:', block[-1].rstrip()[:70])

del lines[s:e]
with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
    f.writelines(lines)
print('已写回，剩余行数:', len(lines))
