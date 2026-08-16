from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Protocol


CREDENTIAL_FILE_NAME = "comfyui_credentials.dat"
CREDENTIAL_SCHEMA_VERSION = 1
MAX_PROTECTED_FILE_BYTES = 64 * 1024
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class CredentialStoreError(RuntimeError):
    """A safe credential-storage failure that never includes secret data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DataProtector(Protocol):
    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, data: bytes) -> bytes: ...


@dataclass(slots=True)
class StoredComfyUICredential:
    base_url: str = field(repr=False)
    client_id: str = field(repr=False)
    client_credential: str = field(repr=False)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class WindowsDpapiProtector:
    """Protect data with Windows DPAPI in the current-user scope."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise CredentialStoreError(
                "credential_unavailable",
                "Windows DPAPI is unavailable on this platform.",
            )
        try:
            self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError):
            raise CredentialStoreError(
                "credential_unavailable",
                "Windows DPAPI could not be initialized.",
            ) from None

        blob_pointer = ctypes.POINTER(_DataBlob)
        self._crypt32.CryptProtectData.argtypes = [
            blob_pointer,
            wintypes.LPCWSTR,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            blob_pointer,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_pointer,
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    @staticmethod
    def _input_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(data, max(1, len(data)))
        blob = _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        return blob, buffer

    def protect(self, data: bytes) -> bytes:
        input_blob, input_buffer = self._input_blob(data)
        output_blob = _DataBlob()
        result = self._crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "Local Prompt Studio ComfyUI credential",
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        del input_buffer
        if not result:
            error_number = ctypes.get_last_error()
            if output_blob.pbData:
                self._kernel32.LocalFree(
                    ctypes.cast(output_blob.pbData, ctypes.c_void_p)
                )
            raise CredentialStoreError(
                "credential_unavailable",
                f"Windows DPAPI protection failed (error {error_number}).",
            )
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if output_blob.pbData:
                self._kernel32.LocalFree(
                    ctypes.cast(output_blob.pbData, ctypes.c_void_p)
                )

    def unprotect(self, data: bytes) -> bytes:
        input_blob, input_buffer = self._input_blob(data)
        output_blob = _DataBlob()
        description = wintypes.LPWSTR()
        result = self._crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            ctypes.byref(description),
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        del input_buffer
        if not result:
            error_number = ctypes.get_last_error()
            if description:
                self._kernel32.LocalFree(
                    ctypes.cast(description, ctypes.c_void_p)
                )
            if output_blob.pbData:
                self._kernel32.LocalFree(
                    ctypes.cast(output_blob.pbData, ctypes.c_void_p)
                )
            raise CredentialStoreError(
                "credential_unavailable",
                f"Windows DPAPI decryption failed (error {error_number}).",
            )
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if description:
                self._kernel32.LocalFree(
                    ctypes.cast(description, ctypes.c_void_p)
                )
            if output_blob.pbData:
                self._kernel32.LocalFree(
                    ctypes.cast(output_blob.pbData, ctypes.c_void_p)
                )


class ComfyUICredentialStore:
    """Store one URL-bound ComfyUI credential under the portable data root."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        protector: DataProtector | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / CREDENTIAL_FILE_NAME
        self._protector = protector

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def has_valid_credential(self, normalized_base_url: str) -> bool:
        """Return local usability for one URL without exposing decrypted fields."""
        try:
            credential = self.load(normalized_base_url)
        except CredentialStoreError:
            return False
        credential.base_url = ""
        credential.client_id = ""
        credential.client_credential = ""
        return True

    def _data_protector(self) -> DataProtector:
        if self._protector is None:
            self._protector = WindowsDpapiProtector()
        return self._protector

    def save(
        self,
        normalized_base_url: str,
        client_id: str,
        client_credential: str,
    ) -> None:
        if (
            not isinstance(normalized_base_url, str)
            or not normalized_base_url
            or not isinstance(client_id, str)
            or not client_id
            or len(client_id) > 256
            or not isinstance(client_credential, str)
            or not client_credential
            or len(client_credential) > 512
        ):
            raise CredentialStoreError(
                "credential_persistence_failed",
                "The ComfyUI credential payload is incomplete.",
            )
        try:
            plaintext = json.dumps(
                {
                    "schema_version": CREDENTIAL_SCHEMA_VERSION,
                    "base_url": normalized_base_url,
                    "client_id": client_id,
                    "client_credential": client_credential,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, UnicodeEncodeError):
            raise CredentialStoreError(
                "credential_persistence_failed",
                "The ComfyUI credential payload is invalid.",
            ) from None
        try:
            protected = self._data_protector().protect(plaintext)
        except CredentialStoreError:
            raise CredentialStoreError(
                "credential_persistence_failed",
                "The ComfyUI credential could not be protected.",
            ) from None
        except Exception:
            raise CredentialStoreError(
                "credential_persistence_failed",
                "The ComfyUI credential could not be protected.",
            ) from None
        if (
            not isinstance(protected, bytes)
            or not protected
            or len(protected) > MAX_PROTECTED_FILE_BYTES
        ):
            raise CredentialStoreError(
                "credential_persistence_failed",
                "The protected ComfyUI credential is empty.",
            )

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".comfyui-credentials-",
                suffix=".tmp",
                dir=self.data_dir,
            )
        except OSError:
            raise CredentialStoreError(
                "credential_persistence_failed",
                "The protected ComfyUI credential could not be saved.",
            ) from None
        temporary_path = Path(temporary_name)
        open_descriptor: int | None = descriptor
        try:
            stream = os.fdopen(descriptor, "wb")
            open_descriptor = None
            with stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                pass
            os.replace(temporary_path, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            raise CredentialStoreError(
                "credential_persistence_failed",
                "The protected ComfyUI credential could not be saved.",
            ) from None
        finally:
            if open_descriptor is not None:
                os.close(open_descriptor)
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def load(self, normalized_base_url: str) -> StoredComfyUICredential:
        try:
            with self.path.open("rb") as stream:
                protected = stream.read(MAX_PROTECTED_FILE_BYTES + 1)
        except FileNotFoundError:
            raise CredentialStoreError(
                "credential_unavailable",
                "No paired ComfyUI credential is available.",
            ) from None
        except OSError:
            raise CredentialStoreError(
                "credential_unavailable",
                "The protected ComfyUI credential could not be read.",
            ) from None
        if not protected:
            raise CredentialStoreError(
                "credential_unavailable",
                "The protected ComfyUI credential is empty.",
            )
        if len(protected) > MAX_PROTECTED_FILE_BYTES:
            raise CredentialStoreError(
                "credential_unavailable",
                "The protected ComfyUI credential is too large.",
            )
        try:
            plaintext = self._data_protector().unprotect(protected)
            payload = json.loads(plaintext.decode("utf-8"))
        except CredentialStoreError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            raise CredentialStoreError(
                "credential_unavailable",
                "The protected ComfyUI credential is malformed.",
            ) from None
        except Exception:
            raise CredentialStoreError(
                "credential_unavailable",
                "The protected ComfyUI credential could not be decrypted.",
            ) from None

        if not isinstance(payload, dict) or payload.get("schema_version") != CREDENTIAL_SCHEMA_VERSION:
            raise CredentialStoreError(
                "credential_unavailable",
                "The protected ComfyUI credential schema is unsupported.",
            )
        base_url = payload.get("base_url")
        client_id = payload.get("client_id")
        client_credential = payload.get("client_credential")
        if (
            not isinstance(base_url, str)
            or not base_url
            or not isinstance(client_id, str)
            or not client_id
            or len(client_id) > 256
            or not isinstance(client_credential, str)
            or not client_credential
            or len(client_credential) > 512
        ):
            raise CredentialStoreError(
                "credential_unavailable",
                "The protected ComfyUI credential payload is invalid.",
            )
        if base_url != normalized_base_url:
            raise CredentialStoreError(
                "credential_url_mismatch",
                "The paired ComfyUI credential belongs to a different server.",
            )
        return StoredComfyUICredential(
            base_url=base_url,
            client_id=client_id,
            client_credential=client_credential,
        )

    def delete(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise CredentialStoreError(
                "credential_unavailable",
                "The protected ComfyUI credential could not be removed.",
            ) from None
