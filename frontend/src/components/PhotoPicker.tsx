import { useEffect, useRef, useState, type DragEvent } from 'react'
import { useLang } from '../i18n'

/** Pick a photo from the gallery or the camera (or drop one), with a preview. */
export function PhotoPicker({
  side,
  hint,
  photo,
  disabled,
  onChange,
}: {
  side: string
  hint: string
  photo: File | null
  disabled?: boolean
  onChange: (photo: File | null) => void
}) {
  const { t } = useLang()
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function handleDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files[0]
    if (!disabled && file?.type.startsWith('image/')) onChange(file)
  }

  function remove() {
    onChange(null)
    // Otherwise choosing the same file again wouldn't fire onChange.
    if (input.current) input.current.value = ''
  }

  return (
    <div className="picker">
      <label
        className={`drop${photo ? ' has-photo' : ''}${dragging ? ' dragging' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        {photo ? (
          <Preview photo={photo} alt={t.photoOf(side)} />
        ) : (
          <svg className="drop-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
            <circle cx="12" cy="13" r="3.5" />
          </svg>
        )}
        <span className="drop-side">{side}</span>
        <span className="drop-hint">{photo ? photo.name : hint}</span>
        {!photo && <span className="drop-action">{t.choosePhoto}</span>}
        <input
          ref={input}
          type="file"
          accept="image/*"
          disabled={disabled}
          onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        />
      </label>
      {photo && (
        <button type="button" className="text-button" onClick={remove} disabled={disabled}>
          {t.removePhoto}
        </button>
      )}
    </div>
  )
}

/** The photo shown from a temporary URL, freed when the photo changes. */
function Preview({ photo, alt }: { photo: File; alt: string }) {
  const image = useRef<HTMLImageElement>(null)
  useEffect(() => {
    const url = URL.createObjectURL(photo)
    if (image.current) image.current.src = url
    return () => URL.revokeObjectURL(url)
  }, [photo])
  return <img ref={image} alt={alt} />
}
