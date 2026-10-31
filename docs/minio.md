# MinIO storage for Loki

Follow these steps to setup an existing MinIO installation for Loki.

1. Create a new bucket named `loki-data` (or whatever you like)

1. Create a new Access Key
   - Set the **Name** to `Loki`.
   - Enable the toggle for "Restrict beyond user policy".
   - Add the following to the **Current User Policy** field:

     ```
     {
         "Version": "2012-10-17",
         "Statement": [
             {
                 "Effect": "Allow",
                 "Action": [
                     "s3:DeleteObject",
                     "s3:GetObject",
                     "s3:ListBucket",
                     "s3:PutObject"
                 ],
                 "Resource": [
                     "arn:aws:s3:::loki-data",
                     "arn:aws:s3:::loki-data/*"
                 ]
             }
         ]
     }
     ```

     ![MinIO Loki Access Key Creation](./images/minio-loki-access-key-create.png)

   - Copy the Access Key and Secret Key to the Stack **Environment variables** in Portainer (or in the `.env` file if running locally).
     ```
     #-  - ACCESS_KEY_ID -
     #-    The username for S3 bucket
     ACCESS_KEY_ID=
     #-  - SECRET_ACCESS_KEY -
     #-    The password for S3 bucket
     SECRET_ACCESS_KEY=
     ```
