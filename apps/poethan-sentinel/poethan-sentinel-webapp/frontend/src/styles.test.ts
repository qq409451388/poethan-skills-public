import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

// 隐藏原生控件用的固定写法：input 绝对定位 + opacity:0，靠相邻的 <i>/<span> 做外观。
// 这种 input 必须有一个定位祖先，否则它的包含块会变成视口：
//   - 位置会漂到别处（横向可能差几百像素）；
//   - 不随 .page 这个滚动容器一起滚；
//   - 会把 html 的 scrollHeight 撑到视口的数倍，导致整个 App 被滚走/推出窗口。
// jsdom 没有布局，测不出这个现象，所以在这里直接守住 CSS 不变量。
const css = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8')

const HIDDEN_INPUT_CONTAINERS: Record<string, string> = {
  '.plugin-option input': '.plugin-option',
  '.switch-row input': '.switch-row',
  '.mode-options input': '.mode-options label',
  '.segmented input': '.segmented label',
  '.dropzone > input': '.dropzone',
}

function declarationsFor(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  // 选择器必须紧邻 { 才算这条规则；前面允许注释或 } 收尾。
  const match = css.match(new RegExp(`(?<![\\w-])${escaped}\\s*\\{([^}]*)\\}`))
  return match?.[1] ?? ''
}

describe('隐藏 input 的定位祖先', () => {
  for (const [inputSelector, containerSelector] of Object.entries(HIDDEN_INPUT_CONTAINERS)) {
    it(`${inputSelector} 的容器 ${containerSelector} 必须建立定位上下文`, () => {
      const inputDeclarations = declarationsFor(inputSelector)
      expect(inputDeclarations, `${inputSelector} 规则不存在`).toContain('position: absolute')
      expect(declarationsFor(containerSelector), `${containerSelector} 缺少 position: relative`).toMatch(/position:\s*(relative|absolute|fixed|sticky)/)
    })
  }

  it('没有漏掉其它绝对定位的隐藏 input', () => {
    const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    const offenders = rules
      .filter(([, , declarations]) => /position:\s*absolute/.test(declarations) && /opacity:\s*0/.test(declarations))
      .map(([, selectors]) => selectors.split(',').map((item) => item.trim()).filter((item) => item.endsWith('input')))
      .flat()
      .filter(Boolean)
    for (const selector of offenders) {
      expect(Object.keys(HIDDEN_INPUT_CONTAINERS), `${selector} 未登记，请确认它有定位祖先`).toContain(selector)
    }
  })
})
