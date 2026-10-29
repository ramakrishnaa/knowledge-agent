import { Pipe, PipeTransform } from '@angular/core'
import { marked } from 'marked'

// Renders Markdown to HTML. Bind the result with [innerHTML] so Angular sanitizes it.
@Pipe({ name: 'markdown' })
export class MarkdownPipe implements PipeTransform {
  transform(text: string): string {
    return marked.parse(text, { async: false })
  }
}
