import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { useLang } from '../i18n'
import { CameraDialog } from './CameraDialog'

// Phones and tablets: their file input opens the camera app (full resolution,
// autofocus). Computers: the webcam is opened in the page instead.
const TOUCH = window.matchMedia('(pointer: coarse)').matches

/** Take a photo (camera or webcam), or pick or drop an image, with a preview. */
export function PhotoPicker({
  side,
  hint,
  kind,
  photo,
  disabled,
  onChange,
}: {
  side: string
  hint: string
  /** Which side of the card: the empty box is drawn like it. */
  kind: 'front' | 'back'
  photo: File | null
  disabled?: boolean
  onChange: (photo: File | null) => void
}) {
  const { t } = useLang()
  const hasWebcam = useHasWebcam()
  const [cameraOpen, setCameraOpen] = useState(false)
  const [dragging, setDragging] = useState(false)

  function handlePick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Emptied, so that choosing the same file again still fires onChange.
    event.target.value = ''
    if (file) onChange(file)
  }

  function handleDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files[0]
    if (!disabled && file?.type.startsWith('image/')) onChange(file)
  }

  return (
    <div className="picker">
      <div
        className={`drop ${kind}${photo ? ' has-photo' : ''}${dragging ? ' dragging' : ''}`}
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

        <div className="picker-actions">
          {TOUCH ? (
            <label className="button small">
              <input
                type="file"
                accept="image/*"
                capture="environment"
                disabled={disabled}
                onChange={handlePick}
              />
              {t.takePhoto}
            </label>
          ) : (
            hasWebcam && (
              <button
                type="button"
                className="button small"
                disabled={disabled}
                onClick={() => setCameraOpen(true)}
              >
                {t.takePhoto}
              </button>
            )
          )}
          <label className="button small secondary">
            <input type="file" accept="image/*" disabled={disabled} onChange={handlePick} />
            {TOUCH ? t.fromGallery : t.uploadPhoto}
          </label>
        </div>
        {!TOUCH && !photo && <span className="drop-hint">{t.dropHere}</span>}
      </div>

      {photo && (
        <button
          type="button"
          className="text-button"
          onClick={() => onChange(null)}
          disabled={disabled}
        >
          {t.removePhoto}
        </button>
      )}

      {cameraOpen && (
        <CameraDialog
          title={t.cameraTitle(side)}
          onCapture={(file) => {
            setCameraOpen(false)
            onChange(file)
          }}
          onClose={() => setCameraOpen(false)}
        />
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

/** Whether the computer has a camera the page may use (only on https or localhost). */
function useHasWebcam() {
  const [has, setHas] = useState(false)
  useEffect(() => {
    const media = navigator.mediaDevices
    if (TOUCH || !media?.getUserMedia) return
    let current = true
    const check = () =>
      media.enumerateDevices().then(
        (devices) => current && setHas(devices.some((device) => device.kind === 'videoinput')),
        () => {},
      )
    check()
    media.addEventListener('devicechange', check)
    return () => {
      current = false
      media.removeEventListener('devicechange', check)
    }
  }, [])
  return has
}
