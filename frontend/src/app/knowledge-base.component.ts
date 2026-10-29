import { Component, inject, signal } from '@angular/core'
import { ApiService } from './api.service'
import { DocumentInfo } from './models'

// Sidebar panel listing the RAG documents, with upload, paste-text and delete.
@Component({
  selector: 'app-knowledge-base',
  template: `
    <label class="upload secondary" [class.disabled]="uploading()">
      {{ uploading() ? 'Indexing…' : '+ Upload files' }}
      <input type="file" accept=".txt,.md,.pdf,.csv,.json" multiple hidden [disabled]="uploading()" (change)="upload($event)" />
    </label>
    @if (pasting()) {
      <form class="paste" (submit)="$event.preventDefault(); savePasted()">
        <input placeholder="Title, e.g. Meeting notes" [value]="title()" (input)="title.set($any($event.target).value)" />
        <textarea rows="6" placeholder="Paste text here…" [value]="text()" (input)="text.set($any($event.target).value)"></textarea>
        <div class="paste-actions">
          <button type="button" class="secondary" (click)="pasting.set(false)">Cancel</button>
          <button type="submit" [disabled]="uploading() || !title().trim() || !text().trim()">Add</button>
        </div>
      </form>
    } @else {
      <button class="secondary paste-toggle" (click)="pasting.set(true)">+ Paste text</button>
    }
    @if (error()) {
      <div class="error">{{ error() }}</div>
    }
    <ul class="doc-list">
      @for (doc of documents(); track doc.source) {
        <li>
          <span class="doc-name" [title]="doc.source">{{ doc.source }}</span>
          <span class="muted">{{ doc.chunks }} chunk{{ doc.chunks === 1 ? '' : 's' }}</span>
          <button class="icon" title="Delete" (click)="remove(doc.source)">×</button>
        </li>
      } @empty {
        <li class="muted">No documents yet. Upload .txt, .md, .pdf, .csv or .json files, or paste text.</li>
      }
    </ul>
  `,
})
export class KnowledgeBaseComponent {
  private api = inject(ApiService)
  protected documents = signal<DocumentInfo[]>([])
  protected uploading = signal(false)
  protected error = signal('')
  protected pasting = signal(false)
  protected title = signal('')
  protected text = signal('')

  constructor() {
    this.refresh()
  }

  async refresh() {
    this.documents.set(await this.api.listDocuments().catch(() => []))
  }

  async upload(event: Event) {
    const input = event.target as HTMLInputElement
    const files = Array.from(input.files ?? [])
    input.value = '' // allow re-uploading the same file name
    if (!files.length) return
    await this.index(async () => {
      const errors: string[] = []
      for (const file of files) {
        await this.api.uploadDocument(file).catch((err) => errors.push(`${file.name}: ${err.message}`))
      }
      if (errors.length) throw new Error(errors.join('\n'))
    })
  }

  async savePasted() {
    await this.index(async () => {
      await this.api.addTextDocument(this.title().trim(), this.text())
      this.title.set('')
      this.text.set('')
      this.pasting.set(false)
    })
  }

  private async index(work: () => Promise<void>) {
    this.uploading.set(true)
    this.error.set('')
    try {
      await work()
    } catch (err: any) {
      this.error.set(err.message)
    } finally {
      await this.refresh()
      this.uploading.set(false)
    }
  }

  async remove(source: string) {
    await this.api.deleteDocument(source).catch(() => {})
    await this.refresh()
  }
}
