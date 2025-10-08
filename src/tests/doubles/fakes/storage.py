from typing import Dict, Union

from storages import S3StorageInterface


class FakeS3Storage(S3StorageInterface):
    """
    Fake S3 Storage class for unit testing.

    This class simulates an S3 storage by storing files in an internal dictionary
    instead of actually uploading them to a remote server.
    """

    def __init__(self):
        """
        Initialize the fake storage with an empty dictionary.
        """
        self.storage: Dict[str, bytes] = {}

    async def upload_file(self, file_name: str, file_data: Union[bytes, bytearray]) -> None:
        """
        Simulates file upload to S3 by storing the file data in a dictionary.

        :param file_name: The name of the file to be stored.
        :param file_data: The file data in bytes.
        """
        self.storage[file_name] = file_data

    async def get_file_url(self, file_name: str) -> str:
        """
        Generates a fake URL for a stored file.

        :param file_name: The name of the file.
        :return: The full fake URL to access the file.
        """
        return f"http://fake-s3.local/{file_name}"
