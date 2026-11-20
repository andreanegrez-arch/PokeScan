// Take a photo with the computer's camera (webcam), inside the page.
//
// Desktop browsers ignore the `capture` attribute of file inputs, so the
// camera is opened with getUserMedia. Phones don't need this: their file
// input opens the camera app, which takes better photos than a video frame.
import { useEffect, useRef, useState } from 'react'
import { useLang, type Strings } from '../i18n'

const JPEG_QUALITY = 0.92

export function CameraDialog({
  title,
  onCapture,
  onClose,
}: {
  title: string
  onCapture: (photo: File) => void
  onClose: () => void
}) {
  const { t } = useLang()
  const dialog = useRef<HTMLDialogElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const still = useRef<HTMLCanvasElement>(null)
  // null: let the browser pick (the rear camera, where there is one).
  const [deviceId, setDeviceId] = useState<string | null>(null)
  const [cameras, setCameras] = useState<string[]>([])
  const [live, setLive] = useState<string | null>(null) // id of the camera streaming
  const [error, setError] = useState<unknown>(null)
  const [taken, setTaken] = useState(false)

  useEffect(() => {
    // Not closed on cleanup: that would fire onClose. Removing it from the page is enough.
    if (!dialog.current?.open) dialog.current?.showModal()
  }, [])

  useEffect(() => {
    let stream: MediaStream | undefined
    let stopped = false
    const which = deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } }
    navigator.mediaDevices
      // The sharpest the camera can do: the code at the bottom of the card is small.
      .getUserMedia({ audio: false, video: { ...which, width: { ideal: 3840 }, height: { ideal: 2160 } } })
      .then(
        async (started) => {
          if (stopped) {
            started.getTracks().forEach((track) => track.stop())
            return
          }
          stream = started
          if (video.current) video.current.srcObject = started
          setLive(started.getVideoTracks()[0]?.getSettings().deviceId ?? '')
          // Device ids are only known once the camera is allowed.
          const devices = await navigator.mediaDevices.enumerateDevices()
          if (!stopped) {
            setCameras(devices.filter((d) => d.kind === 'videoinput').map((d) => d.deviceId))
          }
        },
        (failure) => {
          if (!stopped) setError(failure)
        },
      )
    return () => {
      stopped = true
      stream?.getTracks().forEach((track) => track.stop())
    }
  }, [deviceId])

  function capture() {
    const source = video.current
    const canvas = still.current
    if (!source?.videoWidth || !canvas) return
    canvas.width = source.videoWidth
    canvas.height = source.videoHeight
    canvas.getContext('2d')?.drawImage(source, 0, 0)
    setTaken(true)
  }

  function usePhoto() {
    still.current?.toBlob(
      (blob) => {
        if (!blob) return
        const time = new Date().toTimeString().slice(0, 8).replaceAll(':', '')
        onCapture(new File([blob], `camera-${time}.jpg`, { type: 'image/jpeg' }))
      },
      'image/jpeg',
      JPEG_QUALITY,
    )
  }

  function switchCamera() {
    const next = cameras[(cameras.indexOf(live ?? '') + 1) % cameras.length]
    setLive(null)
    setTaken(false)
    setDeviceId(next)
  }

  return (
    <dialog ref={dialog} className="camera" aria-labelledby="camera-title" onClose={onClose}>
      <h2 id="camera-title">{title}</h2>

      {error ? (
        <p className="error" role="alert">
          {cameraError(t, error)}
        </p>
      ) : (
        <>
          <div className="camera-view">
            <video ref={video} autoPlay playsInline muted hidden={taken} />
            <canvas ref={still} hidden={!taken} />
            {live !== null && !taken && <div className="camera-guide" aria-hidden="true" />}
            {live === null && <p className="camera-status">{t.cameraStarting}</p>}
          </div>
          <p className="fine-print">{taken ? t.checkPhoto : t.cameraHint}</p>
        </>
      )}

      <div className="camera-actions">
        {taken ? (
          <>
            <button type="button" className="primary" onClick={usePhoto}>
              {t.usePhoto}
            </button>
            <button type="button" className="button secondary" onClick={() => setTaken(false)}>
              {t.retake}
            </button>
          </>
        ) : (
          !error && (
            <button type="button" className="primary" disabled={live === null} onClick={capture}>
              {t.capture}
            </button>
          )
        )}
        {!taken && !error && cameras.length > 1 && (
          <button type="button" className="button secondary" onClick={switchCamera}>
            {t.switchCamera}
          </button>
        )}
        <button type="button" className="text-button" onClick={() => dialog.current?.close()}>
          {t.cancel}
        </button>
      </div>
    </dialog>
  )
}

function cameraError(t: Strings, error: unknown) {
  const name = (error as { name?: string } | null)?.name
  if (name === 'NotAllowedError' || name === 'SecurityError') return t.cameraDenied
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return t.cameraMissing
  if (name === 'NotReadableError' || name === 'AbortError') return t.cameraBusy
  return t.cameraFailed(error instanceof Error ? error.message : String(error))
}
