import { Component, input } from '@angular/core'
import { JsonPipe } from '@angular/common'
import { ToolPart, formatOutput } from './models'

@Component({
  selector: 'app-tool-card',
  imports: [JsonPipe],
  template: `
    <details [class]="'tool tool-' + part().status">
      <summary>
        {{ icons[part().status] }} <code>{{ part().name }}</code>
        @if (part().status === 'running') {
          <span class="muted"> running…</span>
        }
      </summary>
      @if (part().input !== undefined) {
        <div class="label">Input</div>
        <pre>{{ part().input | json }}</pre>
      }
      @if (part().output !== undefined) {
        <div class="label">Output</div>
        <pre>{{ formatOutput(part().output) }}</pre>
      }
    </details>
  `,
})
export class ToolCardComponent {
  part = input.required<ToolPart>()
  protected readonly icons = { running: '⏳', done: '✅', error: '⚠️' }
  protected readonly formatOutput = formatOutput
}
