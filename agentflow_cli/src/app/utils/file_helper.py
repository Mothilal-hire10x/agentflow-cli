from typing import Final

from fastapi import HTTPException, UploadFile
from injectq import singleton

from agentflow_cli.src.app.core import logger


try:
    from textxtract import AsyncTextExtractor
except ImportError:
    logger.warning("textxtract not installed. File extraction functionality will be limited.")
    AsyncTextExtractor = None  # type: ignore


@singleton
class FileHelper:
    """Helper class for processing and extracting text from files and text inputs.

    This class provides methods to:
    - Extract text from uploaded files (PDF, DOC, DOCX, TXT, RTF)
    - Process and validate raw text inputs
    - Handle various text encodings
    - Validate file types and sizes

    Attributes:
        MAX_FILE_SIZE: Maximum allowed file size in bytes (10MB)
        SUPPORTED_TEXT_EXTENSIONS: Tuple of supported plain text file extensions
        SUPPORTED_DOC_EXTENSIONS: Tuple of supported document file extensions
        SUPPORTED_EXTENSIONS: All supported file extensions
    """

    # Constants
    MAX_FILE_SIZE: Final[int] = 10 * 1024 * 1024  # 10MB in bytes
    SUPPORTED_TEXT_EXTENSIONS: Final[tuple[str, ...]] = (".txt", ".rtf")
    SUPPORTED_DOC_EXTENSIONS: Final[tuple[str, ...]] = (".pdf", ".doc", ".docx")
    SUPPORTED_EXTENSIONS: Final[tuple[str, ...]] = (
        SUPPORTED_TEXT_EXTENSIONS + SUPPORTED_DOC_EXTENSIONS
    )

    # Encoding fallbacks for text files
    ENCODING_FALLBACKS: Final[tuple[str, ...]] = ("utf-8", "latin-1", "cp1252", "iso-8859-1")

    async def prepare_text(
        self,
        text: str | None = None,
        file: UploadFile | None = None,
    ) -> str:
        """Extract and prepare text from input sources with validation.

        Args:
            text: Optional raw text content
            file: Optional file upload object

        Returns:
            Processed and validated text content

        Raises:
            ValueError: For input validation errors
            HTTPException: For file processing errors
        """
        logger.debug(
            f"Preparing text... has_text: {text is not None}, has_file: {file is not None}"
        )

        # Input validation
        if not text and not file:
            logger.error("Neither text nor file provided")
            raise ValueError("Either 'text' or 'file' must be provided")

        if text and file:
            logger.warning("Both text and file provided, prioritizing text input")

        if text:
            return self._process_text_input(text)

        return await self._process_file_input(file)

    async def prepare_text_from_files(
        self,
        files: list[UploadFile],
    ) -> list[str]:
        """Extract and prepare text from multiple files.

        Args:
            files: List of file upload objects

        Returns:
            List of processed text content from each file

        Raises:
            ValueError: For input validation errors
            HTTPException: For file processing errors
        """
        if not files:
            logger.error("No files provided")
            raise ValueError("At least one file must be provided")

        results = []
        for idx, file in enumerate(files):
            try:
                logger.debug(f"Processing file {idx + 1}/{len(files)}: {file.filename}")
                text = await self._process_file_input(file)
                results.append(text)
            except Exception as e:
                logger.error(f"Failed to process file {file.filename}: {e}")
                raise HTTPException(
                    status_code=400, detail=f"Failed to process file '{file.filename}': {e!s}"
                )

        logger.info(f"Successfully processed {len(results)} files")
        return results

    def _process_text_input(self, text: str) -> str:
        """Process and validate raw text input.

        Args:
            text: Raw text string to process

        Returns:
            Cleaned and normalized text

        Raises:
            ValueError: If text is empty after processing
        """
        logger.debug("Processing raw text input")
        text = text.strip()
        text = " ".join(text.split())  # Normalize whitespace

        if not text:
            logger.error("Provided text is empty after processing")
            raise ValueError("Provided text is empty after processing")

        logger.debug(f"Processed text input with length: {len(text)} characters")
        return text

    async def _process_file_input(self, file: UploadFile) -> str:
        """Process and validate file input.

        Args:
            file: Uploaded file object

        Returns:
            Extracted text content from the file

        Raises:
            HTTPException: For various file processing errors
        """
        try:
            if not file.filename:
                logger.error("File upload missing filename")
                raise HTTPException(status_code=400, detail="File must have a filename")

            logger.debug(f"Processing file: {file.filename}")
            self._validate_file_type(file.filename)

            file_content = await file.read()
            self._validate_file_size(file_content, file.filename)

            if file.filename.lower().endswith(self.SUPPORTED_TEXT_EXTENSIONS):
                return self._decode_text_file(file_content, file.filename)

            return await self._extract_text_from_file(file_content, file.filename)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing file {file.filename}: {e!s}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Error processing file '{file.filename}': {e!s}"
            )
        finally:
            await file.close()
            logger.debug(f"Closed file: {file.filename}")

    def _validate_file_type(self, filename: str) -> None:
        """Validate the file type against supported extensions.

        Args:
            filename: Name of the file to validate

        Raises:
            HTTPException: If file type is not supported
        """
        if not any(filename.lower().endswith(ext) for ext in self.SUPPORTED_EXTENSIONS):
            supported_list = ", ".join(self.SUPPORTED_EXTENSIONS)
            logger.error(f"Unsupported file type: {filename}")
            raise HTTPException(
                status_code=415, detail=f"Unsupported file type. Supported types: {supported_list}"
            )
        logger.debug(f"File type validation passed for: {filename}")

    def _validate_file_size(self, file_content: bytes, filename: str) -> None:
        """Validate the file size against maximum limit.

        Args:
            file_content: Raw file content bytes
            filename: Name of the file for logging

        Raises:
            HTTPException: If file is too large or empty
        """
        file_size = len(file_content)
        logger.debug(f"Validating file size for {filename}: {file_size/1024:.2f}KB")

        if file_size > self.MAX_FILE_SIZE:
            max_size_mb = self.MAX_FILE_SIZE / 1024 / 1024
            logger.error(f"File too large: {filename} ({file_size/1024/1024:.2f}MB)")
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size is {max_size_mb:.1f}MB",
            )

        if file_size == 0:
            logger.error(f"Empty file uploaded: {filename}")
            raise HTTPException(status_code=400, detail="File is empty")

        logger.debug(f"File size validation passed for: {filename}")

    def _decode_text_file(self, file_content: bytes, filename: str) -> str:
        """Decode text file content with multiple encoding fallbacks.

        Args:
            file_content: Raw file content bytes
            filename: Name of the file for logging

        Returns:
            Decoded text content

        Raises:
            HTTPException: If unable to decode with any supported encoding
        """
        logger.debug(f"Attempting to decode text file: {filename}")

        for encoding in self.ENCODING_FALLBACKS:
            try:
                decoded = file_content.decode(encoding)
                logger.debug(f"Successfully decoded {filename} using {encoding}")
                return decoded
            except (UnicodeDecodeError, LookupError):
                logger.debug(f"Failed to decode {filename} with {encoding}")
                continue

        logger.error(f"Unable to decode text file with any supported encoding: {filename}")
        raise HTTPException(
            status_code=400,
            detail=f"Unable to decode text file. Tried encodings: "
            f" {', '.join(self.ENCODING_FALLBACKS)}",
        )

    async def _extract_text_from_file(self, file_content: bytes, filename: str) -> str:
        """Extract text from non-text files using textxtract.

        Args:
            file_content: Raw file content bytes
            filename: Name of the file for logging

        Returns:
            Extracted text content

        Raises:
            HTTPException: If extraction fails or textxtract is not available
        """
        if AsyncTextExtractor is None:
            logger.error("AsyncTextExtractor not available - textxtract not installed")
            raise HTTPException(
                status_code=500,
                detail="Text extraction not available. Please install textxtract library.",
            )

        try:
            logger.debug(f"Extracting text from document: {filename}")
            extractor = AsyncTextExtractor()
            extracted_text = await extractor.extract(source=file_content, filename=filename)

            if not extracted_text or not extracted_text.strip():
                logger.warning(f"No text extracted from file: {filename}")
                raise HTTPException(
                    status_code=400, detail=f"No text could be extracted from the file '{filename}'"
                )

            logger.info(f"Successfully extracted {len(extracted_text)} characters from {filename}")
            return extracted_text.strip()

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Text extraction failed for {filename}: {e!s}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to extract text from '{filename}': {e!s}"
            )
